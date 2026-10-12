import base64
import dataclasses
import io
import json
import pathlib
import subprocess
import sys

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from studio.services import gateway, inference
from studio.services.analysis import analyze
from studio.services.overlay import draw_boxes
from studio.services.render import encode_png, render_png, render_rgb
from studio.services.spend import SpendLedger
from tests.analysis.fake_gateway import FakeGateway, page_of
from tests.analysis.test_fields import FORM

ROOT = pathlib.Path(__file__).resolve().parents[2]
CANARY = "CANARY-7f3a-answer-bearing"
CONFIG = inference.RunConfig(
    route=gateway.Route("openai/gpt-4.1", "byok"),
    roles=["Client", "CounterSigner"],
    signer_mode="synthetic",
    workers=2,
)


SERIAL = dataclasses.replace(CONFIG, workers=1)
INVALID = {
    "candidate_id": None,
    "type": "text",
    "role": "sender",  # only merge fields belong to the sender
    "merge_name": None,
    "required": False,
    "box": {"left": 10, "top": 10, "width": 100, "height": 20},
    "evidence": "synthetic",
}


def run(tmp_path, fake, source=None, run_cap=1.0, config=CONFIG):
    source = source or make_source()
    book = SpendLedger(tmp_path / "ledger.jsonl", "run-1", run_cap)
    return inference.run(
        source, analyze(source), config, token="t", ledger=book, run_id="run-1", transport=fake
    )


def edited(source, change):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(source)))
    change(writer.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def stages(stage_a_fields, stage_b_fields):
    def answer(body):
        self_check = "changes" in body["response_format"]["json_schema"]["schema"]["properties"]
        out = {"fields": stage_b_fields if self_check else stage_a_fields, "notes": "n"}
        return {**out, "changes": []} if self_check else out

    return answer


def page_calls(fake):
    return [page_of(body) for body in fake.chat_bodies() if page_of(body) is not None]


def make_source():
    from tests.analysis.pdf_factory import make_pdf

    return make_pdf([FORM, "A page of plain prose with no fields."])


def test_every_page_drafted_and_checked_is_complete(tmp_path):
    record = run(tmp_path, FakeGateway())
    assert record["status"] == "COMPLETE" and record["profile"] == "PROVISIONAL_DEVELOPMENT"
    form, prose = record["evidence"]
    assert form["stage_a"] == form["stage_b"] == "RECEIVED" and form["inspected"]
    assert prose["stage_a"] == "RECEIVED" and prose["stage_b"].startswith("NOT_NEEDED")
    assert len(record["stage_a"]["fields"]) == len(record["stage_b"]["fields"]) == 1
    assert record["observed_routes"] == [["openai/gpt-4.1", "openai", "byok"]]
    assert record["spend"]["run_usd"] > 0
    assert record["preflight"]["status"] == "PASSED"
    assert record["stage_a"]["notes"] == {1: "", 2: ""}


def test_the_route_is_proven_without_page_content_before_any_page_is_sent(tmp_path):
    system = [{"provider": "openai", "credentialType": "system", "success": True}]
    fake = FakeGateway(attempts=system)
    record = run(tmp_path, fake)
    assert record["status"] == "ROUTE_VIOLATION"
    (preflight,) = fake.chat_bodies()  # the preflight only: no page was sent
    assert page_of(preflight) is None and "Name:" not in json.dumps(preflight)
    assert record["preflight"]["status"].startswith("ROUTE_VIOLATION")
    assert all(e["stage_a"].startswith("NOT_RUN") for e in record["evidence"])


def test_a_route_violation_mid_run_sends_no_further_page(tmp_path):
    def fallback_on_pages(body):
        if page_of(body) is not None:
            return gateway.RouteViolation("credential system was used, expected byok")
        return None

    fake = FakeGateway(fail=fallback_on_pages)  # gateway tests cover detecting a fallback
    record = run(tmp_path, fake, config=SERIAL)
    assert record["status"] == "ROUTE_VIOLATION" and page_calls(fake) == [1]


def test_a_fatal_gateway_error_stops_the_run(tmp_path):
    def rejected(body):
        if page_of(body) is not None:
            return gateway.GatewayError("HTTP 401 from the gateway", fatal=True)
        return None

    fake = FakeGateway(fail=rejected)
    record = run(tmp_path, fake, config=SERIAL)
    assert record["status"] == "PARTIAL" and record["stop_reason"].startswith("gateway")
    assert page_calls(fake) == [1]
    assert record["evidence"][1]["stage_a"].startswith("NOT_RUN")


def test_a_network_error_fails_only_that_page(tmp_path):
    def flaky(body):
        if page_of(body) == 1:
            return gateway.GatewayError("no response from the gateway (URLError)")
        return None

    record = run(tmp_path, FakeGateway(fail=flaky), config=SERIAL)
    form, prose = record["evidence"]
    assert record["status"] == "PARTIAL" and record["stop_reason"] == ""
    assert form["stage_a"].startswith("FAILED") and prose["inspected"]


def test_an_unexpected_error_stops_the_run_and_still_writes_a_record(tmp_path):
    def broken(body):
        return RuntimeError("synthetic") if page_of(body) is not None else None

    fake = FakeGateway(fail=broken)
    record = run(tmp_path, fake, config=SERIAL)
    assert record["status"] == "PARTIAL" and "RuntimeError" in record["stop_reason"]
    assert page_calls(fake) == [1]


@pytest.mark.parametrize("pages", [[0], [3], [1, 1]])
def test_requested_pages_must_exist_and_be_unique(tmp_path, pages):
    fake = FakeGateway()
    with pytest.raises(ValueError, match="pages"):
        run(tmp_path, fake, config=dataclasses.replace(CONFIG, pages=pages))
    assert fake.requests == []


def test_a_draft_whose_fields_were_all_rejected_is_still_checked(tmp_path):
    record = run(tmp_path, FakeGateway(answer=stages([INVALID], [])))
    form, prose = record["evidence"]
    assert prose["stage_b"] == "RECEIVED" and prose["inspected"]
    assert len(record["stage_a"]["rejected"]) == 2 and record["status"] == "COMPLETE"


def test_rejected_final_fields_keep_the_run_partial(tmp_path):
    record = run(tmp_path, FakeGateway(answer=stages([], [INVALID])))
    assert record["status"] == "PARTIAL"
    assert [e["final_rejected"] for e in record["evidence"]] == [1, 0]  # prose: no check


def test_a_route_violation_outranks_any_other_stop_reason():
    stop = inference._Stop()
    stop.set("budget: synthetic")
    stop.set("route violation: synthetic")
    stop.set("gateway: synthetic")
    assert stop.reason == "route violation: synthetic"


def test_a_call_that_costs_more_than_reserved_stops_the_run(tmp_path):
    fake = FakeGateway(market=0.9)
    record = run(tmp_path, fake, run_cap=5.0)
    assert record["status"] == "BUDGET_STOPPED" and page_calls(fake) == []
    assert "above its reserved worst case" in record["stop_reason"]


def test_a_rotated_page_is_not_submitted_and_keeps_the_run_partial(tmp_path):
    fake = FakeGateway()
    record = run(tmp_path, fake, source=edited(make_source(), lambda page: page.rotate(90)))
    assert record["evidence"][0]["stage_a"].startswith("NOT_RUN: rotated")
    assert 1 not in page_calls(fake) and record["status"] == "PARTIAL"


def test_a_cropped_page_is_placed_in_its_visible_area(tmp_path):
    def crop(page):
        page.cropbox = RectangleObject([50, 100, 450, 700])

    record = run(tmp_path, FakeGateway(), source=edited(make_source(), crop))
    form = record["evidence"][0]
    assert form["image_size_px"] == [768, 1152] and "media box" in form["geometry_note"]
    page = record["stage_b"]["envelope"]["pages"][0]
    assert (page["width"], page["height"]) == (400.0, 600.0)
    (field,) = record["stage_b"]["fields"]
    assert abs(field["x"] - (71 - 50)) <= 1  # the rule, measured from the visible area


def test_a_failed_self_check_leaves_the_page_without_final_fields(tmp_path):
    record = run(tmp_path, FakeGateway(fail_stage_b=True))
    assert record["status"] == "PARTIAL"
    assert record["evidence"][0]["stage_b"].startswith("FAILED")
    assert len(record["stage_a"]["fields"]) == 1
    assert record["stage_b"]["fields"] == []  # stage A is never substituted
    assert 1 not in record["pages_with_final_fields"]


def test_hitting_the_run_cap_stops_the_run_before_the_call(tmp_path):
    fake = FakeGateway()
    record = run(tmp_path, fake, run_cap=0.0001)
    assert record["status"] == "BUDGET_STOPPED" and fake.chat_bodies() == []
    assert all(not e["inspected"] for e in record["evidence"])


def test_a_route_violation_stops_the_run(tmp_path):
    system = [{"provider": "openai", "credentialType": "system", "success": True}]
    record = run(tmp_path, FakeGateway(attempts=system))
    assert record["status"] == "ROUTE_VIOLATION" and "credential system" in record["stop_reason"]


DRIVER = """
import json, os, sys
root, capture, *args = sys.argv[1:]
root = os.path.realpath(root)
opened, recording = [], [True]
def hook(event, hook_args):
    if recording[0] and event == "open" and isinstance(hook_args[0], (str, bytes, os.PathLike)):
        path = os.path.realpath(os.fsdecode(hook_args[0]))
        if path.startswith(root):
            opened.append(path)
sys.addaudithook(hook)
from studio.services import gateway
from tests.analysis.fake_gateway import FakeGateway
fake = FakeGateway()
gateway.urllib_transport = fake
import eval.infer
code = eval.infer.main(args)
recording[0] = False
with open(capture, "w") as handle:
    json.dump({"opened": opened, "requests": fake.requests}, handle)
sys.exit(code)
"""


def test_the_model_receives_only_the_source_and_never_answer_bearing_files(tmp_path):
    """Generator isolation on the actual requests: decoys with a canary sit beside the source."""
    case = (tmp_path / "case").resolve()
    case.mkdir()
    source = case / "source.pdf"
    source.write_bytes(make_source())
    for decoy in ("baseline/fields.json", "overlays/page-1.json", "evaluator/report.json"):
        (case / decoy).parent.mkdir()
        (case / decoy).write_text(json.dumps({"fields": CANARY}))
    (case / "source.fields.json").write_text(CANARY)
    private = tmp_path / "private"
    private.mkdir()
    capture, out = private / "capture.json", private / "run.json"
    args = [str(source), "--model", "openai/gpt-4.1", "--credential", "byok"]
    args += ["--roles", "Client,CounterSigner", "--signer-mode", "synthetic", "--run-cap", "1"]
    args += ["--ledger", str(private / "ledger.jsonl"), "--out", str(out)]
    env = {"PATH": "/usr/bin:/bin", "VERCEL_OIDC_TOKEN": "synthetic-token-9d2c"}

    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, str(case), str(capture), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    captured = json.loads(capture.read_text())
    assert captured["opened"] == [str(source)]

    chats = [body for url, body, _ in captured["requests"] if url == gateway.CHAT_URL]
    assert [page_of(body) for body in chats].count(None) == 1  # the route preflight
    preflight = next(body for body in chats if page_of(body) is None)
    assert preflight["messages"][-1]["content"] == inference.PREFLIGHT_TEXT
    chats = [body for body in chats if page_of(body) is not None]
    assert len(chats) == 3  # stage A on both pages, stage B on the form page
    allowed = {"model", "messages", "max_tokens", "temperature", "response_format"}
    for body in chats:
        assert set(body) == allowed | {"providerOptions"}
        assert body["providerOptions"] == {"gateway": {"only": ["openai"]}}
        assert CANARY not in json.dumps(body)

    schemas = [body["response_format"]["json_schema"]["schema"] for body in chats]
    stage_a = [
        body for body, s in zip(chats, schemas, strict=True) if "changes" not in s["properties"]
    ]
    assert len(stage_a) == 2
    for body in stage_a:
        text = body["messages"][1]["content"][0]["text"]
        page = int(text.split("Page ", 1)[1].split(".", 1)[0])
        url = body["messages"][1]["content"][1]["image_url"]["url"]
        png = base64.b64decode(url.split(",", 1)[1])
        scale = inference.MODEL_IMAGE_SHORT_SIDE / 612
        assert png == render_png(source.read_bytes(), page, scale)

    # Stage B sees exactly the stage A image with the stage A draft outlined, nothing else.
    record = json.loads(out.read_text())
    (stage_b,) = [b for b, s in zip(chats, schemas, strict=True) if "changes" in s["properties"]]
    scale = inference.MODEL_IMAGE_SHORT_SIDE / 612
    boxes = [
        (n, f["type"], f["x"] * scale, f["y"] * scale, f["width"] * scale, f["height"] * scale)
        for n, f in enumerate(record["stage_a"]["fields"], start=1)
    ]
    expected = encode_png(draw_boxes(render_rgb(source.read_bytes(), 1, scale), boxes))
    url = stage_b["messages"][1]["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == expected

    summary = json.loads(proc.stdout)
    assert summary["status"] == "COMPLETE" and summary["calls"] == 3  # page calls
    assert summary["preflight"] == "PASSED"
    for text in (out.read_text(), (private / "ledger.jsonl").read_text(), proc.stdout, proc.stderr):
        assert "synthetic-token-9d2c" not in text
    assert out.stat().st_mode & 0o777 == 0o400  # owner-only and read-only


def test_a_page_that_breaks_outside_a_call_still_leaves_a_record(tmp_path, monkeypatch):
    def broken(image, boxes):
        raise RuntimeError("synthetic overlay failure")

    monkeypatch.setattr(inference, "draw_boxes", broken)
    record = run(tmp_path, FakeGateway(), config=SERIAL)
    form, prose = record["evidence"]
    assert form["stage_b"].startswith("FAILED: RuntimeError") and not form["inspected"]
    assert record["status"] == "PARTIAL" and "RuntimeError" in record["stop_reason"]


def test_a_run_needs_at_least_one_worker(tmp_path):
    fake = FakeGateway()
    with pytest.raises(ValueError, match="workers"):
        run(tmp_path, fake, config=dataclasses.replace(CONFIG, workers=0))
    assert fake.requests == []


def test_a_run_has_at_most_the_approved_number_of_workers(tmp_path):
    fake = FakeGateway()
    workers = inference.MAX_WORKERS + 1
    with pytest.raises(ValueError, match="workers"):
        run(tmp_path, fake, config=dataclasses.replace(CONFIG, workers=workers))
    assert fake.requests == []


def cli(tmp_path, monkeypatch, *extra, out=None, ledger=None):
    import eval.infer

    fake = FakeGateway()
    monkeypatch.setattr(gateway, "urllib_transport", fake)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "synthetic-token")
    source = tmp_path / "source.pdf"
    source.write_bytes(make_source())
    out = out or tmp_path / "run.json"
    args = [str(source), "--model", "openai/gpt-4.1", "--credential", "byok"]
    args += ["--roles", "Client,CounterSigner", "--signer-mode", "synthetic", "--run-cap", "1"]
    args += ["--ledger", str(ledger or tmp_path / "ledger.jsonl"), "--out", str(out), *extra]
    return eval.infer.main(args), fake, out


def test_the_cli_refuses_an_output_that_is_the_ledger_before_any_call(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="ledger"):
        cli(tmp_path, monkeypatch, out=tmp_path / "same.jsonl", ledger=tmp_path / "same.jsonl")


def test_the_cli_reserves_the_record_before_any_call(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="cannot create"):
        cli(tmp_path, monkeypatch, out=tmp_path / "missing" / "run.json")
    code, fake, out = cli(tmp_path, monkeypatch, "--pages", "7")
    assert code == 2 and fake.requests == []
    assert json.loads(out.read_text())["status"] == "NOT_RUN"
    assert out.stat().st_mode & 0o777 == 0o400


def test_a_preflight_that_did_not_pass_sends_no_page(tmp_path):
    def unreachable(body):
        if page_of(body) is None:
            return gateway.GatewayError("no response from the gateway (URLError)")
        return None

    fake = FakeGateway(fail=unreachable)
    record = run(tmp_path, fake)
    assert page_calls(fake) == [] and record["status"] == "PARTIAL"
    assert record["preflight"]["status"].startswith("FAILED")
    assert record["stop_reason"].startswith("preflight")
    assert all(e["stage_a"].startswith("NOT_RUN") for e in record["evidence"])


def test_the_cli_refuses_a_page_range_beyond_any_document_before_expanding_it():
    import eval.infer

    with pytest.raises(ValueError, match="page range"):
        eval.infer._pages(f"1-{eval.infer.PAGE_LIMIT + 1}")
    assert eval.infer._pages("2-3,1") == [1, 2, 3]


def test_a_refused_page_call_stops_the_run_instead_of_repeating_on_every_page(tmp_path):
    def bad_request(body):
        if page_of(body) is not None:
            return gateway.GatewayError("HTTP 400 from the gateway", status=400)
        return None

    fake = FakeGateway(fail=bad_request)
    record = run(tmp_path, fake, config=SERIAL)
    assert page_calls(fake) == [1] and record["status"] == "PARTIAL"
    assert record["stop_reason"].startswith("gateway: HTTP 400")


FALLBACK = [
    {"provider": "openai", "credentialType": "byok", "success": False, "statusCode": 403},
    {"provider": "openai", "credentialType": "system", "success": True, "statusCode": 200},
]


def test_an_accepted_fallback_continues_and_is_recorded_on_every_call(tmp_path):
    route = gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True)
    config = dataclasses.replace(CONFIG, route=route)
    record = run(tmp_path, FakeGateway(attempts=FALLBACK), config=config)
    assert record["status"] == "COMPLETE"
    assert record["route"] == {
        "model": "openai/gpt-4.1",
        "credential": "byok",
        "accept_fallback": True,
    }
    assert record["preflight"]["fell_back"] is True
    calls = [call for e in record["evidence"] for call in e["calls"]]
    assert calls and all(call["fell_back"] for call in calls)
    assert record["fallback_calls"] == len(calls) + 1  # the preflight too
    assert record["observed_routes"] == [["openai/gpt-4.1", "openai", "system"]]


FALLBACK_ROUTE = dataclasses.replace(
    SERIAL, route=gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True)
)


class OverrunOnPages(FakeGateway):
    """Falls back on every call; a page call then costs more than its reservation."""

    def __call__(self, url, body, headers):
        payload = json.loads(body) if body else None
        page = payload is not None and isinstance(payload["messages"][-1]["content"], list)
        self.kw["market"] = 0.9 if page else 0.002
        return super().__call__(url, body, headers)


def test_a_call_that_fell_back_and_then_failed_is_still_counted(tmp_path):
    preflight = run(tmp_path, FakeGateway(attempts=FALLBACK, market=0.9), config=FALLBACK_ROUTE)
    assert preflight["status"] == "BUDGET_STOPPED"
    assert preflight["preflight"]["fell_back"] is True and preflight["fallback_calls"] == 1
    (tmp_path / "page").mkdir()
    page = run(tmp_path / "page", OverrunOnPages(attempts=FALLBACK), config=FALLBACK_ROUTE)
    assert page["status"] == "BUDGET_STOPPED" and page["preflight"]["status"] == "PASSED"
    assert page["preflight"]["fell_back"] is True and page["fallback_calls"] == 2


def test_a_fallback_that_is_not_accepted_stops_the_run_and_is_counted(tmp_path):
    route = gateway.Route("openai/gpt-4.1", "byok")
    config = dataclasses.replace(SERIAL, route=route)
    record = run(tmp_path, FakeGateway(attempts=FALLBACK), config=config)
    assert record["status"] == "ROUTE_VIOLATION"
    assert record["preflight"]["fell_back"] is True and record["fallback_calls"] == 1


def test_a_run_on_the_requested_key_records_no_fallback(tmp_path):
    record = run(tmp_path, FakeGateway())
    assert record["route"]["accept_fallback"] is False and record["fallback_calls"] == 0
    assert not record["preflight"]["fell_back"]


def test_each_page_call_is_reserved_at_the_size_of_the_image_sent(tmp_path, monkeypatch):
    seen = []
    real = gateway.worst_case_usd

    def spy(route, text_chars, images, max_output_tokens):
        seen.append(list(images))
        return real(route, text_chars, images, max_output_tokens)

    monkeypatch.setattr(gateway, "worst_case_usd", spy)
    record = run(tmp_path, FakeGateway(), config=SERIAL)
    sizes = [e["image_size_px"] for e in record["evidence"]]
    assert seen[0] == []  # the preflight sends no image
    form, prose = sizes
    assert seen[1:] == [[tuple(form)], [tuple(form)], [tuple(prose)]]  # page 1 A and B, page 2 A


def test_the_cli_passes_an_accepted_fallback_and_refuses_it_off_byok(tmp_path, monkeypatch):
    code, _, out = cli(tmp_path, monkeypatch, "--accept-fallback")
    assert json.loads(out.read_text())["route"]["accept_fallback"] is True
    import eval.infer

    args = [str(tmp_path / "source.pdf"), "--model", "openai/gpt-4.1", "--credential", "system"]
    args += ["--roles", "Client", "--signer-mode", "synthetic", "--run-cap", "1"]
    args += ["--ledger", str(tmp_path / "l2.jsonl"), "--out", str(tmp_path / "r2.json")]
    with pytest.raises(SystemExit, match="only a byok route"):
        eval.infer.main([*args, "--accept-fallback"])
    assert not (tmp_path / "r2.json").exists()


def test_the_reserved_image_size_is_read_from_the_png_sent():
    image = render_rgb(make_source(), 1, 1.0)
    assert inference._png_size(encode_png(image)) == (image.width, image.height)
    with pytest.raises(ValueError, match="not a PNG"):
        inference._png_size(b"GIF89a" + bytes(30))
