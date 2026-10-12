import io
import json
import threading
import urllib.error
import urllib.request
from decimal import ROUND_CEILING, Decimal

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
    assert result.observed_credential == "byok" and result.market_cost_usd == Decimal("0.0042")
    assert [url for url, _, _ in fake.requests][1].startswith(gateway.GENERATION_URL)
    assert book.totals() == (0.0042, 0.0042)


def test_an_unknown_market_cost_is_settled_at_the_worst_case(tmp_path):
    book = ledger(tmp_path)
    call(book, FakeGateway(market=0, lookup=0), worst=0.05)
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["usd"] == "0.05" and settle["market_cost_known"] is False


def test_a_silent_fallback_to_system_credentials_is_a_route_violation_after_settling(tmp_path):
    attempts = [
        {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 403},
        {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
    ]
    book = ledger(tmp_path)
    with pytest.raises(gateway.RouteViolation, match="credential system"):
        call(book, FakeGateway(attempts=attempts, cost=0.001, market=0.001))
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["credential"] == "system" and settle["usd"] == "0.001"
    assert settle["fell_back"] is True  # what happened, whether or not it was accepted


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


@pytest.mark.parametrize("status", [None, 408, 499, 500, 502, 503])
def test_a_failed_call_that_was_not_refused_keeps_its_worst_case(tmp_path, status):
    def broken(url, body, headers):  # a timeout or a 5xx may still have been billed
        raise gateway.GatewayError(f"HTTP {status} from the gateway: synthetic", status=status)

    book = ledger(tmp_path)
    with pytest.raises(gateway.GatewayError) as error:
        call(book, broken, worst=0.07)
    assert book.totals() == (0.07, 0.07)
    assert not error.value.refused and not error.value.fatal


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


def test_network_errors_become_gateway_errors(monkeypatch):
    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("synthetic: connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(gateway.GatewayError) as error:
        gateway.urllib_transport(gateway.CHAT_URL, b"{}", {})
    assert not error.value.fatal


def test_an_http_error_keeps_only_its_status_and_error_code(monkeypatch):
    body = b'{"error": {"type": "authentication_error", "message": "Bearer abc.def.ghi rejected"}}'

    def denied(*args, **kwargs):
        raise urllib.error.HTTPError(gateway.CHAT_URL, 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    with pytest.raises(gateway.GatewayError) as error:
        gateway.urllib_transport(gateway.CHAT_URL, b"{}", {})
    assert str(error.value) == "HTTP 401 from the gateway (authentication_error)"
    assert error.value.status == 401 and error.value.refused
    assert error.value.fatal  # a bad token fails every call: stop, do not retry page by page


@pytest.mark.parametrize("reply", [b"not json", b'{"choices": []}', b'{"choices": [{}]}'])
def test_a_malformed_response_is_fatal_and_keeps_the_worst_case(tmp_path, reply):
    book = ledger(tmp_path)
    with pytest.raises(gateway.GatewayError) as error:
        call(book, lambda url, body, headers: reply, worst=0.05)
    assert error.value.fatal and "route cannot be checked" in str(error.value)
    assert book.totals() == (0.05, 0.05)


def test_the_ledger_adds_money_exactly(tmp_path):
    book = ledger(tmp_path, run_cap=0.3)
    for _ in range(3):
        book.reserve(0.1, "exact")  # binary floats would refuse the third: 0.30000000000000004
    with pytest.raises(BudgetExceeded):
        book.reserve(0.000001, "over")


def test_the_ledger_file_is_owner_only(tmp_path):
    book = ledger(tmp_path)
    book.reserve(0.01, "mode")
    assert book.path.stat().st_mode & 0o777 == 0o600


def test_a_call_that_costs_more_than_its_reservation_stops_the_run(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(BudgetExceeded, match="above its reserved worst case"):
        call(book, FakeGateway(market=0.5), worst=0.05)
    assert book.totals() == (0.5, 0.5)  # the real cost is recorded, not the estimate


def test_an_attempt_on_another_provider_is_a_violation_even_when_it_failed(tmp_path):
    attempts = [
        {"provider": "azure", "credentialType": "byok", "success": False, "statusCode": 500},
        {"provider": "openai", "credentialType": "byok", "success": True, "statusCode": 200},
    ]
    with pytest.raises(gateway.RouteViolation, match="provider azure"):
        call(ledger(tmp_path), FakeGateway(attempts=attempts))


def test_a_failed_attempt_on_the_other_credential_is_recorded(tmp_path):
    attempts = [
        {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 404},
        {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
    ]
    result = gateway.call(
        gateway.Route("openai/gpt-4.1", "system"),
        MESSAGES,
        SCHEMA,
        token="synthetic-token",
        ledger=ledger(tmp_path),
        label="test",
        worst_case=0.05,
        max_output_tokens=100,
        transport=FakeGateway(attempts=attempts),
    )
    assert result.failed_attempts == ["openai/byok/404"]
    assert not hasattr(result, "gateway_cost_usd")


def test_money_stays_decimal_from_the_wire_to_the_ledger(tmp_path):
    book = ledger(tmp_path)
    worst = gateway.worst_case_usd(ROUTE, 3000, [(768, 994)], 100)
    assert isinstance(worst, Decimal) and worst == worst.quantize(Decimal("0.000001"))
    call(book, FakeGateway(market=0.001), worst=worst)
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["usd"] == "0.001" and settle["market_cost"] == "0.001"


@pytest.mark.parametrize("status", [400, 401, 429])
def test_a_call_the_gateway_refused_settles_at_zero(tmp_path, status):
    def refused(url, body, headers):
        raise gateway.GatewayError(f"HTTP {status} from the gateway", status=status)

    book = ledger(tmp_path)
    with pytest.raises(gateway.GatewayError) as error:
        call(book, refused, worst=0.07)
    assert book.totals() == (0.0, 0.0)
    (settle,) = [row for row in rows(book) if row["kind"] == "settle"]  # settled exactly once
    assert settle["usd"] == "0" and settle["refused"] == f"HTTP {status} from the gateway"
    assert error.value.fatal  # a refusal would repeat on every page, so it stops the run


@pytest.mark.parametrize("model", ["openai/gpt-6-astra", "openai/gpt-5.5"])
def test_the_models_alex_excluded_are_not_offered(model):
    with pytest.raises(ValueError, match="no price"):
        gateway.Route(model, "system")


def test_the_gpt5_models_are_reasoning_models():
    for model in ("openai/gpt-5", "openai/gpt-5-mini", "openai/gpt-5-nano", "openai/gpt-5.6-sol"):
        body = gateway.request_body(gateway.Route(model, "byok"), MESSAGES, SCHEMA, 9)
        assert body["reasoning"]["effort"] == "medium" and "temperature" not in body


def test_an_accepted_fallback_to_system_credentials_is_recorded_and_passes(tmp_path):
    attempts = [
        {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 403},
        {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
    ]
    book = ledger(tmp_path)
    result = gateway.call(
        gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True),
        MESSAGES,
        SCHEMA,
        token="synthetic-token",
        ledger=book,
        label="test",
        worst_case=0.05,
        max_output_tokens=100,
        transport=FakeGateway(attempts=attempts, cost=0.001, market=0.001),
    )
    assert result.fell_back and result.observed_credential == "system"
    assert result.failed_attempts == ["openai/byok/403"]
    settle = [row for row in rows(book) if row["kind"] == "settle"][0]
    assert settle["fell_back"] is True and settle["usd"] == "0.001"


def test_a_call_on_the_requested_key_did_not_fall_back(tmp_path):
    route = gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True)
    result = gateway.call(
        route,
        MESSAGES,
        SCHEMA,
        token="synthetic-token",
        ledger=ledger(tmp_path),
        label="test",
        worst_case=0.05,
        max_output_tokens=100,
        transport=FakeGateway(),
    )
    assert not result.fell_back and result.observed_credential == "byok"


BYOK_FAILED = {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 403}


@pytest.mark.parametrize(
    ("kwargs", "problem"),
    [
        ({"model": "openai/gpt-5"}, "model openai/gpt-5 answered"),
        ({"model_attempts": 2}, "2 model attempts"),
        (
            {
                "attempts": [
                    BYOK_FAILED,
                    {"provider": "azure", "credentialType": "system", "success": True},
                ]
            },
            "provider azure",
        ),
        (
            {"attempts": [BYOK_FAILED, {"provider": "openai", "success": True}]},
            "credential unknown",
        ),
        (
            {"attempts": [{"provider": "openai", "credentialType": "system", "success": True}]},
            "credential system was used with no failed byok attempt",
        ),
    ],
)
def test_an_accepted_fallback_excuses_nothing_but_the_credential(tmp_path, kwargs, problem):
    attempts = [
        BYOK_FAILED,
        {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
    ]
    kwargs = {"attempts": attempts, **kwargs}
    with pytest.raises(gateway.RouteViolation, match=problem):
        gateway.call(
            gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True),
            MESSAGES,
            SCHEMA,
            token="synthetic-token",
            ledger=ledger(tmp_path),
            label="test",
            worst_case=0.05,
            max_output_tokens=100,
            transport=FakeGateway(**kwargs),
        )


def test_only_a_byok_route_can_accept_a_fallback():
    with pytest.raises(ValueError, match="byok"):
        gateway.Route("openai/gpt-4.1", "system", accept_fallback=True)
    with pytest.raises(ValueError, match="true or false"):
        gateway.Route("openai/gpt-4.1", "byok", accept_fallback="yes")


def test_o4_mini_is_offered_as_a_reasoning_model():
    route = gateway.Route("openai/o4-mini", "byok")
    assert gateway.PRICES[route.model] == (Decimal("0.0000011"), Decimal("0.0000044"))
    assert "reasoning" in gateway.request_body(route, MESSAGES, SCHEMA, 9)


@pytest.mark.parametrize(
    ("model", "size", "tokens"),
    [
        ("openai/gpt-4.1", (768, 994), 85 + 170 * 4),  # 2 x 2 tiles of 512 px
        ("openai/gpt-5", (768, 994), 70 + 140 * 4),
        ("openai/gpt-4.1", (2000, 3000), 85 + 170 * 6),  # to 1365 x 2048, then 768 x 1152
        ("openai/gpt-4.1", (300, 200), 85 + 170),  # never enlarged
        ("openai/gpt-4.1", (769, 1026), 85 + 170 * 6),  # 768 x 1024.67 counts as 1025 rows
        ("openai/gpt-5.2", (768, 994), 922),  # 24 x 32 patches x 1.2, rounded up
        ("openai/gpt-5-nano", (768, 994), 1152),  # x 1.5
        ("openai/o4-mini", (768, 994), 1321),  # x 1.72
        ("openai/gpt-6-luna", (768, 994), 1890),  # not in the guide: x 2.46, the largest
        ("openai/gpt-6-luna", (40, 40), 85 + 170),  # not in the guide: or a tile, if more
    ],
)
def test_image_tokens_follow_the_published_rule_for_the_image_sent(model, size, tokens):
    assert gateway.image_tokens(model, *size) == tokens


def test_every_offered_model_has_an_image_rule():
    known = set(gateway.TILES) | set(gateway.PATCH_MULTIPLIERS) | gateway.UNPUBLISHED
    assert set(gateway.PRICES) == known
    assert not set(gateway.TILES) & set(gateway.PATCH_MULTIPLIERS)
    assert not gateway.UNPUBLISHED & (set(gateway.TILES) | set(gateway.PATCH_MULTIPLIERS))


def test_a_page_image_is_reserved_at_its_own_size():
    route = gateway.Route("openai/gpt-5.2", "byok")
    price_in, _ = gateway.PRICES[route.model]
    small = gateway.worst_case_usd(route, 0, [(768, 994)], 0)
    large = gateway.worst_case_usd(route, 0, [(2048, 2048)], 0)  # 4,096 patches, uncapped
    for usd, tokens in ((small, 922), (large, 4916)):
        expected = gateway.RESERVE_FACTOR * tokens * price_in
        assert usd == expected.quantize(gateway.MICRO, rounding=ROUND_CEILING)
    assert gateway.worst_case_usd(route, 0, [], 0) == 0
