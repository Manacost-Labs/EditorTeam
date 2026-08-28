# Source Pack update fixture

Previous deliverable: `deliverables/weekly-report.md`

## Source changes

- `S-001`, `pilot-report.pdf`: SHA-256 unchanged; validated extraction may be reused.
- `S-002`, `operations-notes.md`: SHA-256 unchanged; validated chunks may be reused.
- `S-003`, `coverage.csv`: SHA-256 changed; invalidate `CH-S003-01`, `C-011`, and the coverage paragraph.
- `S-004`, `night-shift-scan.pdf`: new source; OCR confidence is low on page 2.

## Current supported claims

- `C-001`: The pilot included 340 requests. Source: `S-001#page=3`. Confidence: high.
- `C-002`: The pilot excluded night shifts. Source: `S-001#page=4`. Confidence: high.
- `C-011`: The corpus coverage is 92%. Source: invalidated because `S-003` changed.

No updated coverage percentage has been approved. The page 2 value in `S-004` must remain unresolved until visual review.
