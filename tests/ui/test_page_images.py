import pypdfium2
import pytest

from studio.services import render
from studio.state import sources
from tests.state.helpers import two_page_pdf

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def sealed(services):
    return sources.seal(services.store, two_page_pdf())["sha256"]


def test_a_page_is_served_as_a_png_rendered_once(local, services, sealed, monkeypatch):
    calls = []
    real = render.render_png
    monkeypatch.setattr(render, "render_png", lambda *a: calls.append(a[1:]) or real(*a))
    first = local.get(f"/api/sources/{sealed}/pages/2.png")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(PNG)
    assert first.headers["cache-control"] == "private, no-store"  # a private document's page
    assert first.headers["x-content-type-options"] == "nosniff"
    again = local.get(f"/api/sources/{sealed}/pages/2.png")
    assert again.content == first.content
    assert calls == [(2, sources.VIEW_SCALE)]  # the second answer was read back, not rendered
    assert services.store.get(f"sources/{sealed}/pages/0002.png").data == first.content


@pytest.mark.parametrize("page", ["0", "3", "-1", "x"])
def test_a_page_outside_the_source_is_not_found(local, sealed, page):
    answer = local.get(f"/api/sources/{sealed}/pages/{page}.png")
    assert answer.status_code in (404, 422)


def test_an_unknown_or_malformed_source_is_not_found(local):
    assert local.get(f"/api/sources/{'0' * 64}/pages/1.png").status_code == 404
    assert local.get("/api/sources/NOT-A-HASH/pages/1.png").status_code == 404


@pytest.mark.parametrize("error", [ValueError("no"), pypdfium2.PdfiumError("no")])
def test_a_page_that_cannot_be_rendered_says_so(local, sealed, monkeypatch, error):
    def refuse(*args):
        raise error

    monkeypatch.setattr(render, "render_png", refuse)
    answer = local.get(f"/api/sources/{sealed}/pages/1.png")
    assert answer.status_code == 422
    assert "could not be rendered" in answer.json()["detail"]


def test_a_large_page_is_rendered_at_a_smaller_scale():
    assert sources.view_scale(612, 792) == sources.VIEW_SCALE
    big = sources.view_scale(5000, 5000)
    assert 0 < big < sources.VIEW_SCALE
    assert 5000 * 5000 * big * big <= sources.MAX_VIEW_PIXELS


def test_a_hosted_page_image_needs_a_session(visitor):
    assert visitor.get(f"/api/sources/{'0' * 64}/pages/1.png").status_code == 401
