# Russian writing profile

Read this reference when the source or requested output is primarily Russian. Use it with the job-specific references from `SKILL.md`; it changes locale decisions, not the preservation or evidence contract.

## Locale contract

Identify the requested variety and destination when they matter: general Russian, organisation house style, legal or regulated text, UI copy, technical documentation, or personal correspondence. Preserve an established house style unless the user asks to change it.

Do not translate fixed product names, commands, identifiers, API terms, filenames, citations, or quoted wording. Preserve Latin and Cyrillic spellings exactly when they are part of an official name or protected literal.

## Intervention rules

- Keep the author's `ё` policy consistent. Do not globally replace `е` with `ё` or remove `ё` unless the user or house style requires it.
- Preserve a deliberate choice between `ты` and `вы`. Capitalise `Вы` only when the source, recipient relationship, or house style supports personal respectful address; do not apply it mechanically to public or product copy.
- Do not erase regional, professional, second-language, or personal phrasing when the reader can follow it and the user did not request standardisation.
- Keep aspect, negation, agency, and modal force intact. A smoother sentence must not change who acts, whether an action completed, or whether it is required, possible, or merely proposed.
- Prefer the smallest edit that restores comprehension. Russian permits flexible word order; change it when emphasis becomes misleading or the dependency chain is hard to parse, not merely to make every sentence subject-verb-object.

## Typography and punctuation

- Use Russian outer quotation marks `«…»`; use `„…“` or `“…”` inside them according to the target house style. Preserve an exact quotation's existing marks when they are protected.
- Distinguish the dash from the hyphen and minus sign. Russian prose uses the dash productively for syntax and emphasis; never treat one dash as an AI signal or import an English-language dash ban.
- Use a non-breaking space where the destination supports it between a number and a short unit, initials and surname, and common abbreviations. Do not change the underlying numeric value or unit.
- Preserve decimal commas, date forms, time notation, and thousands separators from the target locale unless the user requests conversion.
- Follow Russian heading capitalisation: normally capitalise the first word and proper names rather than every content word.
- Do not replace punctuation globally. Choose a colon, dash, comma, conjunction, or full stop from the grammatical relationship and intended emphasis.

## Clarity and naturalness

Look first for structural causes of stiffness:

- long chains of genitives or abstract nouns
- participial and adverbial-participial phrases with an unclear actor
- distant subject and predicate
- several qualifications attached to the wrong clause
- nominal actions where a direct verb would expose the actor
- repeated sentence scaffolds, mirrored paragraphs, or ceremonial openings

Useful repairs include naming the actor, restoring a direct verb, moving the condition beside the rule it qualifies, splitting at a real change of job, or reconnecting fragments that belong to one thought.

Review bureaucratic and promotional wording in context, including `в рамках`, `на сегодняшний день`, `данный`, `осуществляется`, `позволяет`, `является важным`, `играет ключевую роль`, and `открывает новые возможности`. These are review signals, not banned tokens. Keep exact legal, regulatory, administrative, quoted, or writer-owned uses. When a phrase hides meaning, state the actor, action, condition, mechanism, or measured result instead of swapping in a fashionable synonym.

## Uncertainty and evidence

Preserve epistemic markers such as `может`, `возможно`, `вероятно`, `предположительно`, `по оценке`, `примерно`, `около`, `не проверено`, `неизвестно`, and `нет данных`. Do not silently turn:

- `может привести` into `приведёт`
- `вероятно связано` into `вызвано`
- `по предварительной оценке` into `результат составил`
- `не проверено в продакшене` into a production claim

If the wording is ambiguous, retain the narrower claim or surface the uncertainty. Do not add citations, measurements, customer experience, approval, or confidence absent from the source.

## Russian humanisation

Humanisation should restore judgement and specific detail, not simulate casual speech. Avoid automatic slang, filler particles, contractions borrowed from English, fake anecdotes, decorative profanity, or deliberate errors.

For ordinary Russian editing, do not run the English `scripts/scan_aiisms.py` as a quality gate. Diagnose Russian formulae from their function and local context. A future Russian scanner needs its own corpus, exceptions, and evals; translating English regex patterns is not sufficient.

## Completion check

Pass when:

- facts, literals, modality, aspect, negation, and causal direction match the source
- `ты`/`вы`, `е`/`ё`, terminology, typography, dates, and number formats are consistent with the target locale
- punctuation follows Russian grammar and intended emphasis
- no bureaucratic or promotional frame carries a claim that the source cannot support
- the revision still sounds like the writer rather than a generic Russian template
