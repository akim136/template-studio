ICO = b"\x00\x00\x01\x00"
PNG = b"\x89PNG\r\n\x1a\n"


def test_every_page_links_the_homebase_mark(local):
    page = local.get("/")
    assert page.status_code == 200
    assert '<link rel="icon" href="/favicon.ico" sizes="48x48">' in page.text
    assert '<link rel="icon" href="/static/favicon.png" type="image/png">' in page.text
    assert '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">' in page.text
    assert "favicon.svg" not in page.text


def test_the_icons_are_served(local):
    ico = local.get("/favicon.ico")
    assert ico.status_code == 200 and ico.content.startswith(ICO)
    assert ico.headers["content-type"] == "image/x-icon"
    for name in ("favicon.png", "apple-touch-icon.png"):
        image = local.get(f"/static/{name}")
        assert image.status_code == 200 and image.content.startswith(PNG)


def test_the_icon_needs_no_session(visitor):
    assert visitor.get("/favicon.ico", follow_redirects=False).status_code == 200


def test_the_header_shows_the_homebase_mark(local):
    page = local.get("/")
    assert '<img class="brand-mark" src="/static/homebase-mark.png"' in page.text
    assert 'aria-hidden="true">T</span>' not in page.text
    mark = local.get("/static/homebase-mark.png")
    assert mark.status_code == 200 and mark.content.startswith(PNG)
