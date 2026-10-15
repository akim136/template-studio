---
phase: P4
kind: feature
features: F-P4g-inference
---

# Checkbox glyphs in symbol fonts

The candidate finder recognized a checkbox glyph only by its Unicode character. Symbol fonts often
carry no Unicode mapping, so PDFium reads their codes as ordinary characters: Wingdings 2's empty
box reads as "£". Such boxes were not offered to the model as candidates.

A glyph in a symbol font is now a box when its font family and character code name one:
- Wingdings codes 0x6F, 0x71 and 0xA8;
- Wingdings 2 code 0xA3.

This holds both without a Unicode map and when the font maps code c to U+F000 + c, as Word does. The
font is read only for those codes, and the same codes in any other font stay text, such as a pound
sign or the letter "o". The candidate kind stays `box`, so the prompts are unchanged.

`candidates.FINDS` names what the finder looks for, and the drafts' frozen hash now includes it. A
hosted draft started before a change to the finder stops at its next step, instead of drafting some
pages with the old candidates and some with the new.

## Evidence (offline, no model calls)

Candidates on dev-a, dev-b and the synthetic checkbox document, compared with the base:
- dev-b: 8 new box candidates on 2 of 64 pages, all 8 inside reference fields. A census of the
  reference fields that had no candidate (16 across the three documents) found 8 such Wingdings 2
  checkboxes on dev-b.
- dev-a and the synthetic document: identical on every page.
- No candidate was lost, and no page was truncated. Finding candidates took about 5% longer.
- The references are baseline-derived and unreviewed. No live run tested this change.
