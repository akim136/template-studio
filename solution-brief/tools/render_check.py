"""Static checks for the solution brief, and its four-page PDF.

Checks only this brief: the page's sections, claims, links and layout, and the printed PDF. It does
not run the studio, sign in, call a model or touch R2. Writes screenshots, the PDF and report.json
to qa/, which stays out of Git and out of the deployment.
"""

import json
import shutil
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "public"
QA = ROOT / "qa"
PDF = QA / "solution-brief.pdf"
STUDIO = "https://studio.akbuilds.dev/"
QA.mkdir(exist_ok=True)
html = (PUB / "index.html").read_text()
soup = BeautifulSoup(html, "html.parser")
text = soup.get_text(" ", strip=True)
tests = {}


def check(name, value):
    tests[name] = bool(value)


def target_text(index):
    return soup.select("#architecture .arch-card")[index].get_text(" ", strip=True)


SECTIONS = ["overview", "problem", "architecture", "choices", "demo"]
SECTIONS += ["rollout", "success", "risks", "validation"]
for section in SECTIONS:
    check("section_" + section, soup.select_one("#" + section) is not None)
check("correct_current_commit", "60fa80a" in text and "b4a7dfd" in text)
check("no_stale_commit", "d64f620" not in text and "a15cac6" not in text)
check(
    "pr_status",
    "PRs #13–#29, #31 and #34 merged" in text
    and "PRs #13–#29 and #31 merged" not in text
    and "#13–#24" not in text,
)
architecture = soup.select_one("#architecture").get_text(" ", strip=True)
check("correct_storage", "Cloudflare R2" in architecture and "immutable revisions" in text)
check("no_target_worker", "Worker" not in target_text(1))
check("no_target_d1", "D1" not in target_text(1))
check("no_sandbox_features", "Sandbox" not in target_text(1))
check("in_app_simulation", "In-app adapter" in text and "simulated" in text.lower())
check(
    "sponsor_picker",
    "fictional demo customers" in text and "Enter a sponsor name" not in text,
)
check("ai_gateway_openai", "AI Gateway" in text and "OpenAI" in text)
check("spend_cap", "$20 spend cap" in text and "$10 spend cap" not in text)
check("fixed_model", "gpt-5.4-mini" in text and "no model selector" in text)
check("provisional_scores", "provisional" in text.lower() and "UNREVIEWED" in text)
check("no_reviewed_reference_claim", "Against reviewed references" not in text)
check("no_unadjudicated_claim", "not adjudicated" not in text)
check(
    "executed_tests",
    "698 pytest tests" in text and "700 pytest tests" in text and "676 pytest" not in text,
)
check("preview_shared_bucket", "synthetic-only R2 bucket" in text)
check("browser_driven_limit", "while a Studio page remains open" in text)
check("no_chatgpt_credit", "ChatGPT" not in text and "Codex" not in text)
check("public_repo_disclosure", "repo is public" in text.lower() and "Repository · Public" in text)
check("no_private_repo_claim", "repo is private" not in text.lower())
check("distinct_access", "Vercel Authentication is off" in text and "not authorization" in text)
check("unchecked_page_ack", "each page AI left unchecked" in text)
check("config_change_stop", "stops a running draft" in text)
check(
    "headtohead_status",
    "made more errors than Opus" in text
    and "in progress; no result yet" not in text
    and "has finished; the write-up" not in text,
)
check("fluid_enabled_only", "Fluid Compute:" in text and "benefit not verified" in text)
check("three_prim", len(soup.select(".choices-table tbody tr")) == 3)
check("studio_cta", soup.select_one("#studio-cta").get("href") == STUDIO)
check("no_vercel_app_hosts", "vercel.app" not in html)
check("no_pdf_links", not soup.select('a[href$=".pdf"]'))
external = "script[src],link[rel=stylesheet],link[rel=preload]"
check("no_external_assets", len(soup.select(external)) == 0)
ids = [node.get("id") for node in soup.select("[id]")]
anchors = [a.get("href")[1:] for a in soup.select('a[href^="#"]')]
check("all_targets_resolved", all(anchor in ids for anchor in anchors))
check("unique_ids", len(ids) == len(set(ids)))

js_errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"]
    )
    page = browser.new_page(viewport={"width": 1440, "height": 950}, device_scale_factor=1)
    page.on("pageerror", lambda error: js_errors.append(str(error)))
    page.set_content(html, wait_until="load")
    for width in (320, 390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 950})
        page.evaluate("window.scrollTo(0,0)")
        page.wait_for_timeout(180)
        overflow = page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
        check(f"no_overflow_{width}", not overflow)
        hidden = 'el=>getComputedStyle(el).display==="none"'
        check(f"web_continuity_{width}", page.locator("#sheet-1 .page-head").evaluate(hidden))
        page.screenshot(path=str(QA / f"web-{width}.png"), full_page=True)
    check("print_button", page.locator("#print-brief").count() == 1)
    page.pdf(path=str(PDF), format="A4", print_background=True, prefer_css_page_size=True)
    check("no_script_errors", len(js_errors) == 0)
    browser.close()

pdf = pymupdf.open(PDF)
check("four_page_pdf", len(pdf) == 4)
check("pdf_links", sum(len(page.get_links()) for page in pdf) > 12)
first = pdf[0].get_text()
check("page_1_sales", "No founder handoff" in first and "Ready for the" in first)
HEADERS = ["Customer problem", "Vercel choices", "Working demo", "Limitations"]
HEADERS += ["Build & validation"]
check("no_lost_section_headers", all(any(h in page.get_text() for page in pdf) for h in HEADERS))
for number, page in enumerate(pdf, 1):
    image = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False)
    image.save(str(QA / f"page-{number}.png"))
    width, height = page.rect.width + 1, page.rect.height + 1
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    inside = all(b[0] >= -0.5 and b[1] >= -0.5 and b[2] <= width and b[3] <= height for b in blocks)
    check(f"pdf_bounds_{number}", inside)
    check(f"nonempty_page_{number}", len(page.get_text().split()) > 80)

contact = Image.new("RGB", (1580, 2280), "#f1f3f5")
for index in range(min(4, len(pdf))):
    image = Image.open(QA / f"page-{index + 1}.png").convert("RGB")
    image.thumbnail((780, 1100))
    contact.paste(image, (14 + (index % 2) * 790, 18 + (index // 2) * 1125))
contact.save(QA / "contact.png")

out = {
    "scope": "Static brief only. The studio, GitHub, Vercel, OAuth, Gateway and R2 are not tested.",
    "passed": sum(tests.values()),
    "total": len(tests),
    "failed": [name for name, ok in tests.items() if not ok],
    "checks": tests,
    "js_errors": js_errors,
    "pdf_pages": len(pdf),
}
(QA / "report.json").write_text(json.dumps(out, indent=2))
summary = {key: out[key] for key in ("passed", "total", "failed", "pdf_pages")}
print(json.dumps(summary, indent=2))
if out["failed"]:
    raise SystemExit(1)
