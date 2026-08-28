# Project profiles

Use a project profile when multiple deliverables must share the same terminology, evidence boundaries, audience assumptions, voice, privacy rules, or output defaults. A profile reduces repeated briefing; it does not override the user's current request or grant permission to publish, redact, or change external systems.

## Load order

Apply instructions in this order:

1. the user's current request
2. supplied source material and explicit approvals
3. the project profile
4. general Better Writing defaults

When the current request conflicts with the profile, follow the request and mention the exception only when it affects approval, factual scope, legal meaning, privacy, or future reuse. Never silently reconcile incompatible requirements.

## Profile contents

Start from `templates/project-profile.json`. Keep only fields that influence writing decisions:

- project name, description, and default locale
- named audiences and their information needs
- supported deliverable types and default genre
- preferred, protected, and avoided terminology
- source precedence, approved facts, restricted claims, citation style, and uncertainty policy
- high-level voice traits and unwanted habits
- default intervention level and output mode
- sensitive-data categories and publication handling

Do not put credentials, private keys, cookies, access tokens, personal secrets, or large source documents in a project profile. Do not use it as a hidden factual database: every material approved fact still needs a recoverable source locator.

## Validate before reuse

Run:

```bash
python3 scripts/validate_project_profile.py path/to/project-profile.json --gate
```

Resolve errors before applying the profile. Warnings identify choices that may be intentional, such as an empty audience list or a protected term that also appears in an avoid list.

## Apply selectively

- Use the audience entry that matches the actual deliverable; do not merge every audience into one imaginary reader.
- Apply preferred terminology to natural-language prose, not code, identifiers, quotations, legal names, or cited source text.
- Treat protected terms as literal invariants only in the scope stated by the profile.
- Treat restricted claims as claims that require deletion, qualification, or additional evidence—not as banned words.
- Apply voice traits as proportions and tendencies, not as a sentence-by-sentence checklist.
- Use profile defaults only when the user has not selected an intervention level or output mode.

## Evolve safely

When a new preference appears in one request, use it locally first. Add it to the reusable profile only when the user asks to update the profile or the preference is already documented as a project rule. Keep the previous profile version or a reviewable diff when changing evidence, privacy, terminology, or approval rules.

**Complete when:** the applied audience, evidence boundary, terminology, voice, and output defaults are explicit; current-request overrides are visible where material; and the profile contains no sensitive credentials.
