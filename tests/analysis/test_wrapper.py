import ast
import hashlib
import json
import pathlib
import subprocess
import sys

from pypdf.errors import DependencyError

import eval.analyze as wrapper
from studio.services import analysis
from tests.analysis.pdf_factory import make_pdf

ROOT = pathlib.Path(__file__).resolve().parents[2]


def run_wrapper(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "eval.analyze", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_stdout_is_one_json_document_and_diagnostics_go_to_stderr(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(make_pdf(["recovered"], bad_startxref=True))
    proc = run_wrapper(str(source))

    assert proc.returncode == 0, proc.stderr
    record = json.loads(proc.stdout)
    assert record["inventory"] == "COMPLETE"
    assert any("startxref" in line for line in record["diagnostics"])
    assert "diagnostic: " in proc.stderr and "startxref" in proc.stderr


def test_library_output_on_stdout_cannot_break_the_json(tmp_path, monkeypatch, capsys):
    """Regression: a development case's parser printed to stdout and broke its JSON."""
    source = tmp_path / "source.pdf"
    source.write_bytes(make_pdf(["noisy"]))

    def noisy_analyze(data):
        print("parser chatter on stdout")
        return analysis.analyze(data)

    monkeypatch.setattr(wrapper, "analyze", noisy_analyze)
    assert wrapper.main([str(source)]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["page_count"] == 1
    assert "parser chatter on stdout" in err


def test_failures_are_still_json_with_a_nonzero_exit(tmp_path):
    not_pdf = tmp_path / "notes.pdf"
    not_pdf.write_text("plain text")
    for path in (not_pdf, tmp_path / "missing.pdf"):
        proc = run_wrapper(str(path))
        assert proc.returncode == 1
        assert json.loads(proc.stdout)["status"] == "FAILED"


HOOK = """
import json, os, sys
root, source = sys.argv[1], sys.argv[2]
opened, recording = [], [True]
def hook(event, args):
    if recording[0] and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if path.startswith(root):
            opened.append(path)
sys.addaudithook(hook)
import eval.analyze
code = eval.analyze.main([source])
recording[0] = False
print("OPENED=" + json.dumps(opened), file=sys.stderr)
sys.exit(code)
"""


def test_the_wrapper_reads_only_the_source_beside_answer_bearing_files(tmp_path):
    """Generator isolation at runtime: decoy evaluator files next to the source stay unopened."""
    root = tmp_path.resolve()
    source = root / "case" / "source.pdf"
    source.parent.mkdir()
    source.write_bytes(make_pdf(["isolated"]))
    for decoy in ("baseline/fields.json", "overlays/page-1.json", "evaluator/report.json"):
        (root / "case" / decoy).parent.mkdir()
        (root / "case" / decoy).write_text('{"synthetic": true}')

    proc = subprocess.run(
        [sys.executable, "-c", HOOK, str(root), str(source)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    opened = json.loads(proc.stderr.split("OPENED=", 1)[1])
    assert opened == [str(source)]


def test_the_wrapper_has_no_parser_of_its_own():
    """One service: the wrapper calls studio.services.analysis and never imports a PDF library."""
    assert wrapper.analyze is analysis.analyze
    for path in (ROOT / "eval").glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for name in (
                [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            )
        }
        assert not any(name and name.split(".")[0] == "pypdf" for name in imported), path


def test_an_unsupported_dependency_is_failed_json_that_names_the_source(
    tmp_path, monkeypatch, capsys
):
    """An AES-256 source needs a crypto package that has not been adopted."""
    source = tmp_path / "source.pdf"
    source.write_bytes(make_pdf(["locked"]))

    def reader(*args, **kwargs):
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(analysis, "PdfReader", reader)
    assert wrapper.main([str(source)]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "FAILED" and "unsupported" in record["error"]
    assert record["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
