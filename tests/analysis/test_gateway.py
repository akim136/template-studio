import json
import threading

import pytest

from studio.services import gateway
from studio.services.spend import BudgetExceeded, SpendLedger
from tests.analysis.fake_gateway import FakeGateway

ROUTE = gateway.Route("openai/gpt-4.1", "byok")
SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
MESSAGES = [{"role": "user", "content": "synthetic"}]


def ledger(tmp_path, run_cap=1.0, cap=10.0, run="run-1"):
    return SpendLedger(tmp_path / "ledger.jsonl", run, run_cap, cap)


def call(book, transport, worst=0.05):
    return gateway.call(
        ROUTE,
        MESSAGES,
        SCHEMA,
        token="synthetic-token",
        ledger=book,
        label="test",
        worst_case=worst,
        max_output_tokens=100,
        transport=transport,
    )


def rows(book):
    return [json.loads(line) for line in book.path.read_text().splitlines()]


def test_a_byok_call_settles_the_market_cost_from_the_generation_lookup(tmp_path):
    book, fake = ledger(tmp_path), FakeGateway(market=0, lookup=0.0042)
    result = call(book, fake)
    assert result.observed_credential == "byok" and result.market_cost_usd == 0.0042
    assert [url for url, _, _ in fake.requests][1].startswith(gateway.GENERATION_URL)
    assert book.totals() == (0.0042, 0.0042)


def test_an_unknown_market_cost_is_settled_at_the_worst_case(tmp_path):
    book = ledger(tmp_path)
    call(book, FakeGateway(market=0, lookup=0), worst=0.05)
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["usd"] == 0.05 and settle["market_cost_known"] is False


def test_a_silent_fallback_to_system_credentials_is_a_route_violation_after_settling(tmp_path):
    attempts = [
        {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 403},
        {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
    ]
    book = ledger(tmp_path)
    with pytest.raises(gateway.RouteViolation, match="credential system"):
        call(book, FakeGateway(attempts=attempts, cost=0.001, market=0.001))
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["credential"] == "system" and settle["usd"] == 0.001


@pytest.mark.parametrize(
    ("kwargs", "problem"),
    [
        ({"model_attempts": 2}, "2 model attempts"),
        ({"model": "openai/gpt-6-luna"}, "model openai/gpt-6-luna answered"),
        (
            {"attempts": [{"provider": "azure", "credentialType": "byok", "success": True}]},
            "provider azure",
        ),
    ],
)
def test_any_other_model_or_provider_is_a_route_violation(tmp_path, kwargs, problem):
    with pytest.raises(gateway.RouteViolation, match=problem):
        call(ledger(tmp_path), FakeGateway(**kwargs))


def test_a_failed_call_keeps_its_worst_case_reserved(tmp_path):
    def broken(url, body, headers):
        raise gateway.GatewayError("HTTP 502 from the gateway: synthetic")

    book = ledger(tmp_path)
    with pytest.raises(gateway.GatewayError):
        call(book, broken, worst=0.07)
    assert book.totals() == (0.07, 0.07)


def test_a_call_that_could_cross_the_run_cap_is_never_sent(tmp_path):
    fake = FakeGateway()
    with pytest.raises(BudgetExceeded, match="run cap"):
        call(ledger(tmp_path, run_cap=0.01), fake, worst=0.02)
    assert fake.requests == []


def test_the_total_cap_counts_every_run_in_the_ledger(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"kind": "actual", "run": "earlier", "usd": 9.99}) + "\n")
    fake = FakeGateway()
    with pytest.raises(BudgetExceeded, match=r"\$10.00 cap"):
        call(SpendLedger(path, "now", 1.0), fake, worst=0.02)
    assert fake.requests == []


def test_concurrent_reservations_never_exceed_the_cap(tmp_path):
    book, granted = ledger(tmp_path, run_cap=1.0), []

    def reserve():
        try:
            granted.append(book.reserve(0.1, "concurrent"))
        except BudgetExceeded:
            pass

    threads = [threading.Thread(target=reserve) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 10 and book.totals()[1] == pytest.approx(1.0)


def test_the_request_names_one_model_one_provider_and_no_tools():
    body = gateway.request_body(ROUTE, MESSAGES, SCHEMA, 100)
    assert set(body) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "response_format",
        "providerOptions",
    }
    assert body["providerOptions"] == {"gateway": {"only": ["openai"]}}
    assert body["response_format"]["json_schema"]["strict"] is True


def test_a_reasoning_model_gets_an_effort_and_no_temperature():
    body = gateway.request_body(gateway.Route("openai/gpt-6.1-sol", "system"), MESSAGES, SCHEMA, 9)
    assert body["reasoning"] == {"effort": "medium", "exclude": True}
    assert "temperature" not in body


def test_the_token_is_read_without_quotes_and_never_echoed(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    env = tmp_path / ".env.local"
    env.write_text('OTHER=1\nVERCEL_OIDC_TOKEN="synthetic-token"\n')
    assert gateway.read_token(env) == "synthetic-token"
    env.write_text("OTHER=1\n")
    with pytest.raises(gateway.GatewayError) as error:
        gateway.read_token(env)
    assert "synthetic" not in str(error.value)


def test_a_route_needs_a_priced_model_and_a_named_credential():
    with pytest.raises(ValueError, match="no price"):
        gateway.Route("openai/unpriced", "byok")
    with pytest.raises(ValueError, match="credential"):
        gateway.Route("openai/gpt-4.1", "either")
