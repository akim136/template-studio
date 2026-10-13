"""The solution brief stays a static page: no build, no external assets, no PDF in Git."""

import json
import re
from pathlib import Path

BRIEF = Path(__file__).resolve().parents[1] / "solution-brief"


def test_vercel_settings_are_static():
    settings = json.loads((BRIEF / "vercel.json").read_text())
    settings.pop("$schema", None)
    assert settings == {
        "framework": None,
        "buildCommand": "",
        "installCommand": "",
        "outputDirectory": "public",
    }


def test_page_is_self_contained():
    html = (BRIEF / "public" / "index.html").read_text()
    assert not re.search(r"<script[^>]*\bsrc=", html)
    assert not re.search(r"<link[^>]*rel=\"?(stylesheet|preload)", html)
    assert not re.search(r"href=\"[^\"]*\.pdf\"", html)
    assert "vercel.app" not in html


def test_only_the_page_is_published():
    assert [p.name for p in (BRIEF / "public").iterdir()] == ["index.html"]
