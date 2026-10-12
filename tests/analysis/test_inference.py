import base64
import json
import pathlib
import subprocess
import sys

from studio.services import gateway, inference
from studio.services.analysis import analyze
from studio.services.render import render_png
from studio.services.spend import SpendLedger
from tests.analysis.fake_gateway import FakeGateway
from tests.analysis.test_fields import FORM

ROOT = pathlib.Path(__file__).resolve().parents[2]
CANARY = "CANARY-7f3a-answer-bearing"
CONFIG = inference.RunConfig(
    route=gateway.Route("openai/gpt-4.1", "byok"),
    roles=["Client", "CounterSigner"],
    signer_mode="synthetic",
    workers=2,
)


def run(tmp_path, fake, source=None, run_cap=1.0):
    source = source or make_source()
    book = SpendLedger(tmp_path / "ledger.jsonl", "run-1", run_cap)
    return inference.run(
        source, analyze(source), CONFIG, token="t", ledger=book, run_id="run-1", transport=fake
    )


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
opened, recording = [], [True]
def hook(event, hook_args):
    if recording[0] and event == "open" and isinstance(hook_args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(hook_args[0])
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

    summary = json.loads(proc.stdout)
    assert summary["status"] == "COMPLETE" and summary["calls"] == 3
    for path in (out, private / "ledger.jsonl"):
        assert "synthetic-token-9d2c" not in path.read_text()
    assert not out.stat().st_mode & 0o222  # the run record is read-only
