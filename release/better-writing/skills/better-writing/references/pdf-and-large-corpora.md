# PDF and large-corpus workflow

Use this branch when the source material includes PDFs, scans, OCR output, many documents, or a dataset that must become readable prose. The goal is not merely to extract text. The goal is a polished deliverable whose claims can still be traced to the supplied sources.

## Scope boundary

This workflow supports source-backed writing: articles, reports, chapters, briefs, summaries, guides, and editorial compilations. It may organise tables and structured records as evidence for prose.

Use a dedicated data-analysis workflow when the primary job is statistical modelling, aggregation, charting, SQL, or quantitative inference. Use a dedicated PDF workflow or tool to open, render, OCR, repair, or create PDF files. Better Writing governs the editorial synthesis and fidelity of the resulting prose.

## 1. Inventory before extraction

Build a source manifest before reading a large collection. Record, at minimum:

- stable source ID and file path
- file type, byte size, and SHA-256 digest
- page count when available
- born-digital, scanned, OCR, or mixed status
- language and likely reading order
- presence of tables, figures, captions, footnotes, or appendices
- duplicate, skipped, failed, or ready state

For a local collection, run:

```bash
python3 scripts/build_corpus_manifest.py path/to/sources --output corpus-manifest.json
```

Add `--chunks-dir prepared-chunks` to split readable text and Markdown sources into deterministic, bounded chunks. This helper inventories PDFs but deliberately does not pretend to extract or OCR them.

Do not silently skip password-protected, corrupted, empty, unsupported, or unreadable files. Keep them in the manifest with a clear status and error.

**Complete when:** every supplied file is accounted for exactly once as ready, duplicate, skipped, or failed.

## 2. Extract with page provenance

For born-digital PDFs, extract text with page boundaries. For scanned or mixed PDFs, use OCR and retain the page images or rendered pages used to judge the result. Never rely on plain extraction alone to understand columns, tables, forms, sidebars, footnotes, or figure placement.

Give each extracted unit a stable locator such as:

```text
[source: policy-2026.pdf | pages: 14-15 | chunk: 003]
```

Keep the original file, extracted text, and any normalised text as separate layers. Do not overwrite the last known good extraction when a new OCR pass is worse.

Inspect representative rendered pages and every page whose extraction looks suspicious. Sample the beginning, middle, and end of long documents, plus pages with tables, multiple columns, diagrams, unusual fonts, handwriting, or low contrast.

**Complete when:** every usable chunk has a source and page locator, and extraction quality has been checked visually where layout can change meaning.

## 3. Measure extraction quality

Classify each source or page range:

- **high confidence:** normal reading order, intact words, reliable numbers, and visible agreement with the rendered page
- **medium confidence:** usable prose with repairable hyphenation, headers, line breaks, or occasional OCR noise
- **low confidence:** broken reading order, missing regions, ambiguous characters, damaged tables, or material disagreement with the rendered page

Common PDF failures include repeated headers and footers, page numbers inside sentences, merged columns, split words, lost minus signs or decimal separators, misread dates, detached footnotes, and table cells flattened in the wrong order.

Do not silently repair a value when more than one reading is plausible. Use `[неразборчиво]`, preserve alternatives, or mark the claim as unresolved. Ask for a better scan only when the missing material can change the requested result.

## 4. Normalise losslessly

Remove extraction noise only after preserving the raw layer. Safe normalisation may:

- join line wraps inside a paragraph
- remove repeated headers and footers after confirming they are not content
- repair end-of-line hyphenation when the word is unambiguous
- restore paragraph boundaries and list structure
- attach footnote markers to their notes

Never normalise away page locators, units, signs, decimal separators, table headers, quotation boundaries, uncertainty markers, or editorial annotations. Keep visible distinctions between source wording, OCR reconstruction, and editorial paraphrase.

## 5. Work in bounded semantic chunks

Do not load an entire large corpus into one context. Split by real boundaries: document, chapter, section, page range, table, interview, or topic. Use fixed-size character limits only as a secondary guardrail.

For each chunk, produce a compact evidence record:

- source and page range
- topic and role in the final deliverable
- supported facts, quotations, and numbers
- uncertainty, conflicts, and missing context
- table or figure references
- confidence level

Summaries are indexes, not replacement evidence. Return to the source chunk before using a detail in final prose.

## 6. Build the source ledger

Create a ledger before drafting:

| Claim or section | Source locator | Confidence | Conflict or caveat | Used |
|---|---|---|---|---|
| Example finding | `S03, pp. 14-15` | high | pilot only | yes |

When sources disagree, keep both positions and identify the conflict. Do not average, merge, or choose a preferred value without a stated rule. Deduplicate repeated documents by digest, but retain edition and revision differences as separate sources.

For tables, preserve column headers, units, row labels, source, and page. Prefer a structured intermediate form such as CSV or JSON when the table will support multiple claims. Validate totals and representative rows before narrating the data.

**Complete when:** every planned section has evidence, every material conflict is visible, and unsupported gaps are labelled rather than filled.

## 7. Draft from the ledger, not from memory

Choose the target genre and make an outline that maps sections to evidence records. Draft one section at a time. Keep citations or source locators close enough to recover the origin of every material claim.

Turn extraction residue into natural prose:

- combine fragments only when their relationship is supported
- make actors and actions explicit
- translate table structure into sentences without dropping units or scope
- distinguish quotation, paraphrase, inference, and editorial explanation
- preserve the source's uncertainty and jurisdiction, period, population, or sample limits

Beautiful prose is not decorative prose. It is accurate, shaped for the reader, free of OCR debris, and specific about what the sources do and do not establish.

## 8. Reconcile coverage and finish

Before delivery, reconcile the draft against the manifest and source ledger:

1. Every supplied file has a terminal status.
2. Every material claim has a source locator or is explicitly labelled as editorial synthesis.
3. Numbers, dates, names, quotations, units, and uncertainty match the source.
4. Low-confidence OCR does not support an unqualified claim.
5. Conflicting sources remain visible and are not silently harmonised.
6. Tables and figures used in prose were checked against rendered pages.
7. The final structure serves the reader rather than mirroring file order.
8. Failures, omissions, and unresolved questions are listed separately from the clean deliverable when the output mode allows it.

For very large jobs, report operational coverage as counts: discovered, unique, duplicate, processed, skipped, failed, and used. A high file count is not proof of good coverage; show which sources actually support the deliverable.

## Recovery rules

- Resume from the manifest and completed chunk records instead of restarting the whole corpus.
- Write new extraction attempts beside the last known good version.
- Use stable IDs so a revised file invalidates only its own chunks and dependent claims.
- Keep batch outputs deterministic enough to compare between runs.
- If context or time is exhausted, deliver a verified partial result with exact coverage boundaries rather than implying the corpus is complete.
