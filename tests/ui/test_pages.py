import hashlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import app
from studio.state import sources
from studio.state.store import StorageError
from studio.ui import pages
from tests.state.helpers import POLICY, field, two_page_pdf
from tests.state.test_hosted import EMAIL, SAME_ORIGIN, uploaded
from tests.state.test_r2 import CREDENTIALS

TYPED = {
    "title": "Fund II - Subscription",
    "sponsor_name": "Acme Capital",
    "sponsor_account_id": "",
}
COUNTERSIGN = {"countersigner": True, "required": [{"signer": 1, "type": "signature"}]}


def make(services, clock, title, sponsor="Acme Capital", policy=POLICY, fields=()):
    source = sources.seal(services.store, two_page_pdf())
    details = {**TYPED, "title": title, "sponsor_name": sponsor}
    created = services.templates.create(source["sha256"], policy, details)
    if fields:
        services.templates.save(created["id"], 1, policy, list(fields), uuid.uuid4().hex)
    clock.advance(minutes=1)
    return created


def titles(page):
    return re.findall(r'class="open" href="/templates/[0-9a-f]{32}">([^<]*)</a>', page.text)


def test_without_saved_state_the_pages_say_so_and_show_no_sample(monkeypatch):
    monkeypatch.setattr(app.state, "services", None)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    for path in ("/", "/new", "/templates/" + "a" * 32):
        page = client.get(path)
        assert page.status_code == 503 and "nothing can be saved" in page.text.lower()
        assert "sample" not in page.text.lower()
        assert "data-viewer" not in page.text  # nobody to carry drafts on for


def test_local_pages_refuse_another_machine(services, monkeypatch):
    monkeypatch.setattr(app.state, "services", services)
    client = TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.9", 50000))
    assert client.get("/").status_code == 403
    assert client.get("/new").status_code == 403


def test_an_empty_library_says_so(local):
    page = local.get("/")
    assert page.status_code == 200
    assert "No templates yet" in page.text and "0 templates" in page.text
    assert "Local · no sign-in" in page.text
    assert '<body data-viewer="local">' in page.text


def test_the_library_lists_saved_templates_newest_first_with_labels(local, services, clock):
    make(services, clock, "Older draft")
    make(services, clock, "Newer with fields", fields=[field()])
    page = local.get("/")
    assert titles(page) == ["Newer with fields", "Older draft"]
    assert ">Needs review<" in page.text and ">Draft<" in page.text
    assert "Acme Capital" in page.text and "2 templates" in page.text


def test_typed_values_are_escaped(local, services, clock):
    make(services, clock, "<script>alert(1)</script>", sponsor='"><img src=x onerror=alert(1)>')
    page = local.get("/").text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_tabs_search_and_mode_filter(local, services, clock):
    make(services, clock, "Alpha draft")
    make(services, clock, "Beta review", fields=[field()])
    make(services, clock, "Gamma countersign", sponsor="Other LP", policy=COUNTERSIGN)
    drafts = local.get("/?tab=drafts")
    assert sorted(titles(drafts)) == ["Alpha draft", "Gamma countersign"]
    assert titles(local.get("/?tab=review")) == ["Beta review"]
    assert titles(local.get("/?q=other+lp")) == ["Gamma countersign"]
    assert titles(local.get("/?q=BETA")) == ["Beta review"]
    assert titles(local.get("/?mode=countersign")) == ["Gamma countersign"]
    assert sorted(titles(local.get("/?mode=lp"))) == ["Alpha draft", "Beta review"]
    empty = local.get("/?q=nothing-like-this")
    assert "No templates match these filters" in empty.text and "Clear filters" in empty.text


def test_tab_counts_follow_the_search(local, services, clock):
    make(services, clock, "Alpha draft")
    make(services, clock, "Beta review", fields=[field()])
    page = local.get("/?q=beta").text
    counts = dict(re.findall(r"<span>([^<]+)</span><span class=\"count\">(\d+)</span>", page))
    assert counts == {
        "All templates": "1",
        "Drafts": "0",
        "Needs review": "1",
        "Completed": "0",
        "Failed": "0",
    }


def test_sorting_by_title_and_paging(local, services, clock):
    for number in range(12):
        make(services, clock, f"Template {number:02d}")
    first = local.get("/?sort=title&order=asc")
    assert titles(first) == [f"Template {n:02d}" for n in range(10)]
    assert "Showing 1–10 of 12" in first.text
    second = local.get("/?sort=title&order=asc&page=2")
    assert titles(second) == ["Template 10", "Template 11"]
    assert "Showing 11–12 of 12" in second.text
    assert titles(local.get("/?sort=title&order=desc"))[0] == "Template 11"
    assert titles(local.get("/?page=99")) == titles(local.get("/?page=2"))  # clamped


def test_unknown_filter_values_fall_back(local, services, clock):
    make(services, clock, "Only one")
    assert titles(local.get("/?tab=bogus&mode=bogus&sort=bogus&order=sideways")) == ["Only one"]


def test_every_library_view_carries_the_region_a_view_change_swaps(local):
    # studio.js fetches the next view and swaps this one region, so each answer must hold it once.
    for query in ("", "?tab=drafts", "?q=nothing&sort=title&order=asc&page=2"):
        page = local.get(f"/{query}")
        assert page.status_code == 200
        assert page.text.count("data-library") == 1
        assert '<main class="page page-library" data-library>' in page.text


def test_a_library_that_cannot_be_read_says_so(local, services, monkeypatch):
    def broken(prefix):
        raise StorageError("synthetic")

    monkeypatch.setattr(services.store, "children", broken)
    page = local.get("/")
    assert page.status_code == 503 and "Templates could not be loaded" in page.text
    assert "No templates yet" not in page.text


def test_pages_carry_a_strict_content_security_policy(local):
    page = local.get("/")
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "default-src 'none'" in policy
    assert "unsafe-inline" not in policy and "frame-ancestors 'none'" in policy
    assert "connect-src 'self';" in policy  # local mode never talks to R2
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in page.text and " style=" not in page.text  # nothing inline


def test_the_setup_page_uploads_through_the_studio_locally(local):
    page = local.get("/new")
    assert page.status_code == 200
    assert 'data-upload="server"' in page.text
    assert "sample" not in page.text.lower() and "model" not in page.text.lower()


def test_setup_requires_only_the_signatures_by_default(local):
    page = local.get("/new")
    boxes = re.findall(
        r'<input type="checkbox"( checked)? data-required data-signer="(\d)" data-type="(\w+)">',
        page.text,
    )
    checked = {(signer, kind) for on, signer, kind in boxes if on}
    assert len(boxes) == 5 and checked == {("0", "signature"), ("1", "signature")}
    assert "Client · 0" not in page.text and "CounterSigner · 1" not in page.text


def test_the_record_page_shows_saved_state(local, services, clock):
    created = make(services, clock, "Fund II - Subscription")
    page = local.get(f"/templates/{created['id']}")
    assert page.status_code == 200
    assert "Fund II - Subscription" in page.text and ">Draft<" in page.text
    assert "the field map is empty" in page.text  # its review is blocked, and it says why
    assert created["source_sha256"][:12] in page.text


@pytest.mark.parametrize("template_id", ["a" * 32, "not-an-id"])
def test_the_record_page_for_an_unknown_template_is_404(local, template_id):
    page = local.get(f"/templates/{template_id}")
    assert page.status_code == 404 and "No such template" in page.text


def test_static_files_are_served(local):
    for path in ("/static/studio.css", "/static/studio.js", "/static/setup.js"):
        assert local.get(path).status_code == 200, path


def test_scripts_and_styles_are_named_by_this_deploys_version(local):
    # a browser never pairs this deploy's pages with an earlier deploy's cached scripts
    page = local.get("/new")
    linked = re.findall(r'(?:src|href)="(/static/[a-z]+\.(?:js|css)[^"]*)"', page.text)
    assert linked and all(url.endswith(f"?v={pages.ASSET_VERSION}") for url in linked)
    assert re.fullmatch(r"[0-9a-f]{12}", pages.ASSET_VERSION)
    assert local.get(linked[0]).status_code == 200


def test_a_hosted_visitor_without_a_session_is_sent_to_sign_in(visitor):
    for path in ("/", "/new", "/templates/" + "a" * 32):
        answer = visitor.get(path, follow_redirects=False)
        assert answer.status_code == 303 and answer.headers["location"] == "/auth/login"


def test_a_signed_in_browser_sees_its_identity_and_r2_uploads(signed_in, bucket):
    page = signed_in.get("/")
    assert page.status_code == 200 and EMAIL in page.text and "Rae Reviewer" in page.text
    # Drafts this browser carries on are kept per person, named by a digest rather than the email.
    key = hashlib.sha256(EMAIL.encode()).hexdigest()[:16]
    assert f'<body data-viewer="{key}">' in page.text
    assert f"https://{CREDENTIALS.host}" in page.headers["content-security-policy"]
    assert 'data-upload="direct"' in signed_in.get("/new").text


def test_the_hosted_library_lists_from_r2(signed_in, bucket):
    sha256 = uploaded(signed_in, bucket, two_page_pdf())
    assert signed_in.post(f"/api/uploads/{sha256}/seal", headers=SAME_ORIGIN).status_code == 201
    body = {"source_sha256": sha256, "policy": POLICY, "details": TYPED}
    created = signed_in.post("/api/templates", json=body, headers=SAME_ORIGIN).json()
    page = signed_in.get("/")
    assert titles(page) == [TYPED["title"]]
    assert signed_in.get(f"/templates/{created['id']}").status_code == 200


def test_a_browser_back_from_sign_in_lands_on_the_library(signed_in):
    answer = signed_in.get("/auth/me", headers={"accept": "text/html"}, follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == "/"
    assert signed_in.get("/auth/me").json()["email"] == EMAIL  # an API client still gets JSON
