# Homebase Template Studio | Solution Brief

A static, customer-facing solution brief for Vercel's Solutions Architect assessment. It is served
by its own Vercel project, `akbuilds-brief`, at `akbuilds.dev`, and is separate from the protected
studio at `studio.akbuilds.dev`. It has no build step and no access to the app's settings,
secrets or storage.

- `public/index.html`: the brief, with its styles, script and Homebase wordmark inline.
- `SOLUTION_BRIEF.md`: the same content as editable text.
- `tools/render_check.py`: static checks of the page, and its four-page PDF.
- `vercel.json`: settings for `akbuilds-brief` only. The studio's own project is unaffected.

## View locally

```sh
python3 -m http.server 8000 --directory solution-brief/public
```

## Check the page and make the PDF

```sh
cd solution-brief
uv run --no-project --with beautifulsoup4 --with playwright --with pillow --with pymupdf \
    python tools/render_check.py
```

It checks the page's sections, claims, links, anchors and self-containment, and that it has no
horizontal overflow from 320px to 1440px. It prints the page to A4 and checks the PDF: four pages,
text inside each page, and working links. Screenshots, the PDF and `report.json` go to `qa/`, which
stays out of Git and out of the deployment. The PDF is shared directly; the page's **Print brief**
button gives the same four pages. Run the check again after any change to the page.

These checks cover only this brief. They do not run the studio, sign in, call a model or touch R2.

## Vercel project

`akbuilds-brief`: Root Directory `solution-brief`, Output Directory `public`, framework Other, no
build or install command, and no Vercel Authentication. The studio keeps its protection. It
deploys from `main` through Git, and skips the build when `solution-brief/` is unchanged.

## Before publishing on `akbuilds.dev`

1. `studio.akbuilds.dev` serves the studio, and sign-in and upload work there. The domain on the
   studio's project, `STUDIO_PUBLIC_ORIGIN`, the sign-in callback, a production redeploy and the
   R2 CORS origin were done on October 13, 2026.
2. Then move `akbuilds.dev` and `www.akbuilds.dev` to `akbuilds-brief`, with `www` redirecting to
   the apex.
3. If another app change merges, update the source commit, PR range and test count on the page
   and in `SOLUTION_BRIEF.md`, then rerun the check.
