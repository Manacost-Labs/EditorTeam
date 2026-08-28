# Source Packs and claim audits

A Source Pack is a portable, reviewable bundle for source-backed writing. Use it when material arrives in different formats, work must resume later, several deliverables share evidence, or a reviewer must trace claims without reopening the entire raw corpus.

## Source Pack v1

Recommended shape:

```text
source-pack/
|-- source-pack.json
|-- project-profile.json          optional
|-- corpus-manifest.json
|-- claim-ledger.jsonl
|-- issues.jsonl                  optional
|-- raw/                          original sources or stable links
|-- extracted/                    page-marked extraction and OCR
|-- chunks/                       bounded semantic chunks
`-- deliverables/                 drafts and approved outputs
```

Keep layers separate. Raw files are evidence; extracted files are observations; normalised chunks are working material; summaries are indexes; deliverables are editorial outputs. Never overwrite one layer with another.

Start `source-pack.json` from `templates/source-pack.json`. Build the corpus manifest with `scripts/build_corpus_manifest.py`. A Source Pack may reference files outside its directory when copying them is inappropriate, but each reference must be stable and access failures must remain visible.

## Stable identities and incremental updates

Use stable source IDs, SHA-256 digests, page ranges, and chunk IDs. On a later run:

- unchanged digest: reuse validated extraction and chunks
- changed digest: invalidate only that source, its chunks, and dependent claims
- new source: add it without renumbering existing IDs
- missing source: mark it unavailable; do not silently delete its prior claims
- duplicate digest: retain provenance but process the content once

Record the processing tool and version when OCR or extraction quality can change. Preserve the last known good extraction beside new attempts until the replacement passes quality checks.

## Claim ledger

Store one JSON object per line in `claim-ledger.jsonl`. Start from `templates/claim-ledger.jsonl`.

Required fields:

- `claim_id`: stable unique ID
- `text`: the factual claim or labelled editorial synthesis
- `kind`: `source_claim` or `editorial_synthesis`
- `status`: `supported`, `conflicted`, `unresolved`, or `excluded`
- `confidence`: `high`, `medium`, `low`, or `unknown`
- `source_locators`: source IDs plus page, section, table, figure, or chunk
- `material`: whether the claim can change the reader's conclusion or action
- `used_in_draft`: whether the current deliverable uses it

Additional rules:

- A supported source claim used in a draft needs at least one locator.
- A conflicted claim needs at least two locators and `conflict_disclosed: true` before use.
- An unresolved or excluded claim cannot be used in the draft.
- Editorial synthesis used in a draft needs `labelled: true`; it must not masquerade as a sourced fact.
- Low-confidence material may remain in notes, but its use in a deliverable requires visible qualification and manual review.

Run the deterministic audit:

```bash
python3 scripts/check_claim_coverage.py source-pack/claim-ledger.jsonl --gate
```

Add `--strict-confidence` when low-confidence material must block release.

## Coverage report

Report at least:

- discovered, unique, processed, duplicate, skipped, and failed sources
- total, material, used, supported, conflicted, unresolved, and excluded claims
- tracked-claim coverage for the current draft
- sources and claims invalidated since the last run
- low-confidence material and unverified tables or figures

A coverage percentage is a navigation aid, not proof of truth. It says whether used claims are accounted for, not whether sources are correct or the argument is sound.

## Handoff and completion

A Source Pack is ready for handoff when:

1. every supplied source has a terminal or active processing state;
2. every used material claim is sourced or explicitly labelled as synthesis;
3. unresolved and excluded claims are absent from the draft;
4. conflicts used in the draft are disclosed;
5. low-confidence OCR and tables are visible;
6. the manifest, claim ledger, issues, and deliverable agree on coverage;
7. another editor can recover a claim's origin without relying on chat history.
