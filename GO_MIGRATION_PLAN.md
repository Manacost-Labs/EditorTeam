# План перехода EditorTeam на Go-оркестрацию

Статус: аудит завершен 2026-09-03, существующий Python-код пока не изменялся.

## 1. Что уже есть

### Python-сайдкар

- `src/editorteam/server.py` поднимает `ThreadingHTTPServer` и предоставляет `/health`, `/analyze`, `/validate`, `/rules`, `/outline/validate`, а также операции с корпусом.
- `analyze()` вызывает локальные анализаторы из `.claude/skills/hs-edit/scripts`: `markers`, `guide_voice`, `clarity`, `cards`, `consistency`, `structure`, `rhythm`, `soul`.
- `validate()` сохраняет защищенные сущности, числа, коды колод, отрицания, осторожность, ритм, голос, режим GUIDE и покрытие утверждений для переплавки.
- `rules_for()` собирает термины, типографику, профили, скелет, нормы, журнал правок, голос и примеры корпуса.
- `validate_outline()` проверяет план переплавки против профиля и утверждений исходника.

### Текущий Go-код

- `go/cmd/editor-gateway` — HTTP-шлюз с AG-UI, `/edit`, `/audit`, вызовами LLM и циклом повторной правки.
- `go/internal/editor` — режимы `лёгкая`, `обычная`, `глубокая`, `переплавка`, двухпроходная переплавка, retry и возврат исходника при отказе.
- `go/internal/analyzer` — HTTP-клиент Python-сайдкара и типы его результатов.
- `go/internal/llm` — OpenAI-совместимый и AG-UI клиенты.
- `go/internal/api` — лимит тела, graceful HTTP-слой, логирование, AG-UI контекст и Google Docs read-only guard.
- `go/internal/config` — окружение, таймауты, провайдер, лимит текста и секреты без вывода в логи.

## 2. Совместимые Python endpoints

Сохраняются без изменения контрактов:

| Метод и путь | Назначение |
|---|---|
| `GET /health` | состояние сайдкара, игры и профили |
| `POST /analyze` | находки и метрики анализаторов |
| `POST /validate` | затвор до/после, включая `depth`, claims и freshness |
| `POST /rules` | правила, профиль и контекст переплавки |
| `POST /outline/validate` | проверка JSON-плана переплавки |

Операции `/corpus/*` остаются на Python на первом этапе: это управление стилевым корпусом, а не путь запроса редактора.

## 3. Что нельзя потерять

1. Факты исходника: карты, числа, стоимость, коды колод, ссылки, отрицания, советы и условия.
2. Официальные формы `Темные дары`, `Темный дар`, `тип существа`; защищенные `ОТК` и `возвещение`.
3. Режимы `GUIDE`, `ANALYSIS`, `REPORT` и глубины правки.
4. Evidence-hidden правило: в GUIDE research narration не попадает в текст.
5. Сравнение переплавки с нормой автора и claims, а не с длиной и голосом плохого исходника.
6. Возврат исходника после исчерпания попыток; предупреждения не запускают лишний retry.
7. Лимит тела, таймауты, request ID, структурные ошибки, JSON-логи и graceful shutdown.

## 4. Что переносим в Go

Добавляется новый основной бинарник `go/cmd/editorteam` и пакетный слой:

- `internal/pipeline` — отдельные стадии Input → protected entities →
  Natasha/Razdel, Hunspell, LanguageTool, Vale, markdownlint и Python
  preflight → editorial analysis → claims → rewrite/edit → critic → targeted
  repair (не более двух циклов) → повтор всех проверок → semantic validation →
  final.
- `internal/rules` — компактный динамический `RuleBundle` по игре, жанру, режиму, глубине, языку и автору; внутренние пороги и метрики в модель не передаются.
- Игровые конфиги сохраняются отдельно; добавлен пакет `league` с честной
  оговоркой о пока отсутствующем справочнике локализации и предварительных
  нормах, без догадочных замен названий.
- `internal/analyzers` — интерфейс `Analyzer`, встроенный анализатор, Python,
  Natasha/Razdel, Hunspell, markdownlint, LanguageTool и Vale adapters.
- `internal/natasha` и `sidecars/nlp` — отдельный HTTP NLP-сайдкар с точными
  offsets, кэшем SHA-256 и fallback без Natasha.
- `internal/hunspell` — безопасный запуск словаря только для findings; игровые
  allowlist находятся в `config/dictionaries`.
- `internal/markdownlint` — безопасный CLI-adapter для Markdown.
- `evals` — Promptfoo baseline/candidate, 34 обезличенных кейса и
  детерминированные проверки сохранения.
- `internal/language` — безопасный HTTP-клиент LanguageTool `/v2/check` с явным языком и унифицированным `Finding`.
- `internal/vale` — временный файл, `exec.CommandContext` без shell, ограничение вывода и диагностируемое отсутствие CLI.
- `internal/guards` — защита сущностей и проверки исчезновения/изменения чисел, отрицаний, советов, новых фактов, карт, ссылок и Markdown.
- `internal/api` — совместимые `/health`, `/analyze`, `/validate`, `/rules`, `/outline/validate`; текущие `/edit`, `/audit`, `/ag-ui` сохраняются для обратной совместимости.

Сначала Go вызывает существующий Python-сайдкар через безопасный HTTP adapter. Это позволяет сделать API и pipeline рабочими до полной замены морфологических проверок.

## 5. Что временно остается внешним Python

На первом рабочем срезе через `PythonAnalyzerAdapter` вызываются без переписывания:

`claims.py`, `cards.py`, `semantic_diff.py`, `rewrite_gate.py`, `consistency.py`, `structure.py`, `clarity.py`, `soul.py`, `rhythm.py`, а также текущий `server.py` для совместимости.

Второй вариант adapter — контролируемый subprocess для окружений без сайдкара. Он принимает только фиксированную программу и аргументы, использует `exec.CommandContext`, таймаут, ограничение stdout/stderr и мягкую остановку процесса; `sh -c` запрещен.

## 6. Контракт нового pipeline

Модель получает только `RuleBundle` с полями `task`, `editorial_goal`, `style_rules`, `terminology_rules`, `protected_entities`, `source_claims`, `relevant_examples`, `qa_findings`. Не передаются README, архив целиком, внутренние метрики и пороги.

`rewrite/edit` поддерживает `proofread`, `edit`, `rewrite`. `critic` возвращает оценки ясности, структуры, пользы, конкретики, голоса, точности и терминологии; targeted repair запускается максимум два раза. Итог содержит `text`, `mode`, `changes`, `factual_risks`, `qa_findings`, `protected_entities_changed`, `scores`, `accepted`, `provider`, `model`, `prompt_version`.

## 7. Проверки и тесты

- unit-тесты Go для RuleBundle, защиты сущностей, pipeline и анализаторов;
- contract-тесты JSON для пяти совместимых endpoints;
- интеграционные тесты LanguageTool и Vale с `httptest`/временным fake CLI;
- preservation-тесты на числа, ссылки, Markdown, отрицания, uncertainty, советы и отсутствие новых фактов;
- golden-набор из 20–30 реальных текстов плюс плохой AI-текст и хороший авторский текст;
- существующие Python и Go тесты запускаются до и после изменений.

Команды проверки: `go test ./...`, `go vet ./...`, `go build ./cmd/editorteam`, затем `docker compose up --build`.

## 8. Этапы и ограничения

1. Добавить контракт, RuleBundle, adapters и pipeline рядом с текущим шлюзом.
2. Подключить совместимые endpoints и наблюдаемость.
3. Добавить LanguageTool/Vale и Docker-конфигурацию.
4. Переключить Docker gateway на `editorteam`, сохранив `editor-gateway` как совместимую команду на время миграции.
5. После накопления golden-результатов переносить отдельные Python-проверки; удаление текущих анализаторов не планируется.

В эту миграцию не входят frontend, CMS, fine-tuning, отдельный микросервис на каждый анализатор, бесконечный чат, autopublish и удаление корпуса.
