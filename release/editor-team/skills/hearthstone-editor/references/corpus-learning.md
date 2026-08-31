# Continuous Corpus Learning

EditorTeam учится только на опубликованных и явно одобренных человеком гайдах. Черновик или AI-generated text может храниться как `candidate`, но не меняет baseline.

## Два разных слоя

- `STYLE CORPUS`: approved гайды любых лет и патчей. Они дают голос, ритм, термины и структуру.
- `GAME KNOWLEDGE`: только проверенное current evidence для текущих `patch` и `meta_epoch`.

`echo.py` и `precedent.py` помечают архивные примеры как `STYLE ONLY`/`HISTORICAL CONTENT`. Они не могут подтверждать текущую стратегию.

## Добавление

```bash
editor-team corpus add guides/pure-paladin.md \
  --published-at 2026-08-30 \
  --patch 36.4 \
  --author manacost \
  --tag standard \
  --tag paladin \
  --genre constructed-guide
```

Команда проверяет UTF-8, пустой текст, raw и normalized SHA-256, базовую структуру и обычные анализаторы. Находки quality gate показываются как warnings: осознанно необычный гайд не обязан быть «идеальным».

Без `--approve` запись получает статус `candidate`. Явно активировать её можно позже:

```bash
editor-team corpus approve GUIDE_ID
```

Неподходящий candidate можно явно пометить как `rejected`: `editor-team corpus reject GUIDE_ID`. Одобрение разрешено только для source `published` или `final`.

Если человек уже проверил опубликованный текст, можно сразу передать `--approve`. Этот флаг и команда `approve` — единственные способы дать тексту право менять норму.

## Что происходит при активации

1. Собирается candidate state.
2. Пересчитываются global и genre baseline.
3. Строятся median, quartiles, MAD и trimmed mean для длины фраз и абзацев, обращений, императивов, контрастов, коротких фраз, скобок и AI markers.
4. Считаются terminology и heading frequencies.
5. Before/after report показывает изменения и `CORPUS_DRIFT_WARNING`.
6. `selftest.py` запускается на candidate manifest, а не на старом active state.
7. Только после `PASS` manifest, baseline и immutable snapshot публикуются атомарно. При ошибке возвращается `CORPUS_REGRESSION_FAILED`, active version не меняется.

Жанр получает свой baseline при минимум трёх approved guides; до этого использует global fallback.

## Версии, сравнение и rollback

```bash
editor-team corpus inspect
editor-team corpus versions
editor-team corpus compare v12 v13
editor-team corpus rollback v12
editor-team corpus remove GUIDE_ID
```

`remove` не удаляет файл из истории: managed guide получает `archived`, legacy guide попадает в `excluded_legacy_ids`. Затем создаётся новая версия и запускается та же regression gate.

Rollback не переписывает старый snapshot. Он берёт его state, прогоняет regression и создаёт новую active version.

## HTTP API

Локальный sidecar принимает те же операции:

- `POST /corpus/add`
- `POST /corpus/approve`
- `POST /corpus/remove`
- `POST /corpus/reject`
- `POST /corpus/inspect`
- `POST /corpus/versions`
- `POST /corpus/compare`
- `POST /corpus/rollback`

Для `/corpus/add` передаются `path`, `published_at`, `patch` и те же необязательные поля, что в CLI. `approve: true` — явное решение человека, а не авторешение сервиса.
