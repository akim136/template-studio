"""The local studio's journey in a real browser: setup, the record, the editor, review, approval
and the simulated create, on synthetic PDFs."""

import re
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect

from tests.analysis.pdf_factory import make_pdf

RECORD = re.compile(r"/templates/[0-9a-f]{32}$")
SPONSOR = "demo-18614"


def pdf(name: str, pages: int) -> dict:
    texts = [f"Synthetic page {n}" for n in range(1, pages + 1)]
    return {"name": name, "mimeType": "application/pdf", "buffer": make_pdf(texts)}


def follow(page: Page, link: str, url: str) -> None:
    """Clicks a link and waits until the next page has loaded, its scripts included; a click alone
    returns before they run, and input sent before then is lost."""
    page.get_by_role("link", name=link).click()
    page.wait_for_url(url)


def create_template(page: Page, pages: int = 3) -> str:
    """Set up a template in the one-signer mode and return its record URL."""
    page.goto("/")
    expect(page.locator(".empty")).to_contain_text("No templates yet")
    follow(page, "New template", "**/new")
    page.select_option("#sponsor-pick", SPONSOR)
    page.set_input_files("[data-file-input]", pdf("synthetic-lp.pdf", pages))
    expect(page.locator("[data-file-state]")).to_have_text("Sealed")
    page.check("input[name=mode][value=lp]")
    create = page.locator("button[data-create]")
    expect(create).to_be_enabled()
    create.click()
    page.wait_for_url(RECORD)
    return page.url


def add_signature(page: Page, x: int = 200, y: int = 300) -> None:
    page.click("[data-add-toggle]")
    page.locator("[data-add-types] button", has_text="Signature").click()
    expect(page.locator("[data-placing]")).to_contain_text("Click the page")
    page.locator("[data-sheet]").first.click(position={"x": x, "y": y})


def test_a_pdf_goes_from_setup_to_a_created_template(page: Page) -> None:
    record = create_template(page)
    expect(page.get_by_text("Field drafting runs on the hosted studio")).to_be_visible()

    follow(page, "Edit fields", "**/edit")
    image = page.locator("img[data-sheet-image]").first
    expect(image).to_be_visible()
    loaded = "img => img.complete && img.naturalWidth > 0"
    page.wait_for_function(loaded, arg=image.element_handle())
    expect(page.locator("[data-save-label]")).to_have_text("Saved")

    add_signature(page)
    expect(page.locator('.box[data-id^="f-"]')).to_have_count(1)
    expect(page.locator("[data-save-label]")).to_have_text("Unsaved changes")
    page.click("button[data-save]")
    expect(page.locator("[data-save-label]")).to_have_text("Saved")
    expect(page.locator("[data-revision]")).to_have_text("Rev 2")

    page.reload()
    expect(page.locator(".box[data-id]")).to_have_count(1)

    page.click("[data-open-preflight]")
    page.check("[data-pf-confirm]")
    preflight = page.locator("dialog[data-preflight]")
    expect(preflight).to_contain_text("Review confirmed for revision 2")
    page.click("[data-pf-approve]")
    expect(page.locator("[data-cp-title]")).to_have_text("Template created")

    page.goto("/")
    row = page.locator("table.templates tbody tr")
    expect(row).to_have_count(1)
    expect(row.locator("a.open")).to_have_attribute("href", urlsplit(record).path)


def test_setup_refuses_a_file_that_is_not_a_pdf(page: Page) -> None:
    page.goto("/new")
    page.select_option("#sponsor-pick", SPONSOR)
    text = {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"not a pdf"}
    page.set_input_files("[data-file-input]", text)

    expect(page.locator("[data-file-state]")).to_have_text("Unsupported")
    expect(page.locator("[data-file-message]")).to_contain_text("Only PDF files are supported.")
    expect(page.locator("button[data-create]")).to_be_disabled()


def test_review_waits_for_unsaved_changes(page: Page) -> None:
    create_template(page, pages=1)
    follow(page, "Edit fields", "**/edit")
    confirm = page.locator("[data-pf-confirm]")
    close = page.locator("dialog[data-preflight] [data-pf-close]").first

    add_signature(page)
    page.click("button[data-save]")
    expect(page.locator("[data-revision]")).to_have_text("Rev 2")
    page.click("[data-open-preflight]")
    expect(confirm).to_be_enabled()
    close.click()

    add_signature(page, y=400)
    page.click("[data-open-preflight]")
    expect(page.locator("[data-pf-unsaved]")).to_be_visible()
    expect(confirm).to_be_disabled()
    expect(page.locator("[data-approval-label]")).to_have_text("Not reviewed")
    close.click()

    page.click("button[data-save]")
    expect(page.locator("[data-revision]")).to_have_text("Rev 3")
    page.click("[data-open-preflight]")
    expect(confirm).to_be_enabled()
