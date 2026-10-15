"""The shared page script (`static/studio.js`) and what the page scripts take from it. The
drafting page's script runs only on the hosted studio, so no journey here runs it; the second
test checks every page script against what the shared script offers."""

import re

from playwright.sync_api import Page

from tests.e2e.conftest import ROOT
from tests.e2e.test_journey import add_signature, create_template, follow

COMMENT = re.compile(r"(^|\s)//.*", re.M)
USE = re.compile(r"\bstudio\.(\w+)")
TAKEN = re.compile(r"\bconst \{([^}]*)\} = studio;")
SHARED = {"api", "drafting", "el", "fact", "plural", "sleep", "warnBeforeLeaving"}


def test_the_shared_script_offers_the_page_helpers(page: Page) -> None:
    page.goto("/")
    plural = "[studio.plural(1, 'field'), studio.plural(2, 'field')]"
    assert page.evaluate(plural) == ["1 field", "2 fields"]
    button = "studio.el('button', {class: 'button', text: 'Go', disabled: true, hidden: false})"
    assert page.evaluate(f"{button}.outerHTML") == '<button class="button" disabled="">Go</button>'
    facts = """(() => {
        const list = document.createElement("dl");
        studio.fact(list, "Pages", "3", "mono");
        studio.fact(list, "Size", "1 KiB");
        return list.innerHTML;
    })()"""
    expected = '<dt>Pages</dt><dd class="mono">3</dd><dt>Size</dt><dd>1 KiB</dd>'
    assert page.evaluate(facts) == expected
    assert page.evaluate("studio.sleep(1).then(() => 'slept')") == "slept"


def test_page_scripts_use_only_what_the_shared_script_offers(page: Page) -> None:
    used = set()
    for script in (ROOT / "static").glob("*.js"):
        if script.name != "studio.js":
            text = COMMENT.sub(r"\1", script.read_text())
            used |= set(USE.findall(text))
            used |= {name.strip() for names in TAKEN.findall(text) for name in names.split(",")}
    page.goto("/")
    offered = set(page.evaluate("Object.keys(window.studio)"))
    assert SHARED <= used  # the scan sees each way a page script takes a helper
    assert used <= offered, sorted(used - offered)


def test_leaving_the_editor_asks_first_only_with_unsaved_changes(page: Page) -> None:
    create_template(page, pages=1)
    follow(page, "Edit fields", "**/edit")
    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.type), dialog.dismiss()))
    add_signature(page)

    page.close(run_before_unload=True)
    page.wait_for_timeout(500)
    assert asked == ["beforeunload"] and not page.is_closed()

    page.click("button[data-save]")
    page.wait_for_selector("[data-save-label]:text-is('Saved')")
    with page.expect_event("close"):
        page.close(run_before_unload=True)
    assert asked == ["beforeunload"]
