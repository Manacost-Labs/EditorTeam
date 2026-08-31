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

## Отдельный архив Полей сражений из TXT

Массовый экспорт статей импортируется в отдельную коллекцию `corpus-bg`, чтобы исторические советы по Полям сражений не смешивались с constructed-корпусом:

```bash
editor-team corpus import-bg "/path/to/гайды по полям"
editor-team corpus inspect --collection bg
```

Импорт понимает два формата: экспорт Manacost с блоком `--- TEXT ---` и старый экспорт Koloda с разделом `## Текст`. Он:

- рекурсивно учитывает каждый `.txt` как `imported`, `duplicate`, `failed` или `skipped`;
- не меняет исходные файлы;
- сохраняет относительный путь, URL, автора, дату, категории, формат и SHA-256 источника;
- удаляет только известный рекламный хвост ВКонтакте в последних 30 строках;
- сохраняет названия, числа и весь основной текст;
- добавляет весь пакет одной версией, только со статусом `candidate`;
- откладывает тяжёлый quality-анализ до ручного `approve` и хранит `quality_status: pending`;
- ставит `historical: true`, `style_only: true`, `knowledge_eligible: false` и `patch: unknown`.

Поэтому импортированные материалы можно использовать для голоса, структуры, терминологии, примеров объяснения механик и поиска редакционных прецедентов, но нельзя использовать как доказательство актуальной меты. После ручной проверки отдельный материал активируется явно:

```bash
editor-team corpus approve GUIDE_ID --collection bg
editor-team corpus reject GUIDE_ID --collection bg
editor-team corpus versions --collection bg
```

Для `corpus-bg` активация запускает проверку целостности файлов и хешей. Пороги constructed-only `selftest.py` к этой коллекции не применяются.

## Архив обычных гайдов

Для массового архива гайдов по колодам используется отдельная коллекция `corpus-archive`:

```bash
editor-team corpus import-guides "/path/to/old-koloda-articles-guides"
editor-team corpus inspect --collection archive
```

Импорт делает всё из общего TXT-контракта выше, а также:

- учитывает все вложенные файлы, включая `manifest.txt` и служебные, со статусом `imported`, `duplicate`, `failed` или `skipped`;
- отсеивает точные копии по SHA-256 и копии с одинаковым нормализованным текстом;
- сравнивает TXT с PDF-извлечениями из `гайды/` по доле общих 5-словных шинглов; порог 0,90 переживает переносы строк, дефисы и отличия front matter;
- сохраняет `source_id`, URL, относительный путь, дату, категории, формат и SHA-256;
- повторный запуск идемпотентен: уже загруженные материалы становятся `duplicate`, но новая версия corpus не создаётся.

Для `old-koloda-articles-guides` результат зафиксирован в `corpus-archive/SOURCE.json`: 348 файлов учтены полностью, 297 уникальных гайдов добавлены candidates, 49 PDF/TXT-дублей отсеяны, 2 служебных файла пропущены, ошибок нет. Все 297 записей остаются `candidate` и `quality_status: pending`, пока человек не одобрит конкретный текст:

```bash
editor-team corpus approve GUIDE_ID --collection archive
editor-team corpus reject GUIDE_ID --collection archive
editor-team corpus versions --collection archive
```

Коллекция может учить голосу, структуре и терминологии только после approval. Даже approved-текст не подтверждает актуальные карты, баланс или мету.

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
