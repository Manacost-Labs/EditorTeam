# Evaluation через Promptfoo

Быстрый режим `prompt-direct` в `evals/promptfooconfig.yaml` сравнивает два полных
системных prompt: `baseline` воспроизводит бережную редактуру до
нового pipeline, `candidate` добавляет практическую пользу, защиту
авторского голоса и удаление AI-шаблонов. Provider принимает только
точное содержимое этих двух файлов. Произвольный system prompt
отклоняется до вызова модели; production API не принимает system prompt.

Режим `pipeline-e2e` в `evals/promptfooconfig.pipeline.yaml` отправляет исходный
текст в `POST /v2/edit` и тем самым проверяет весь production-конвейер:
analysis, rewrite, critic, targeted repair, postflight и все подключённые
анализаторы. Вариант prompt задаётся только при запуске каждого gateway через
`EDITOR_PROMPT_VARIANT=baseline|candidate`; другое значение останавливает
сервис. Тело `/v2/edit` не содержит поля для system prompt.

В наборе 48 обезличенных кейсов для гайдов, новостей, мета-отчётов,
аналитики, Hearthstone, World of Warcraft и League of Legends. Он включает
сленг, числа, URL, ссылки, таблицы, списки и fenced code blocks. Шесть кейсов
`corpus-05`…`corpus-10` — настоящие обезличенные фрагменты авторского корпуса
`гайды/` по 600–1100 знаков с заголовками, жирным и защищёнными названиями
карт; поле `vars.origin` помечает их. Они проверяют не удаление дефекта, а
сохранение живого текста: `remove_ai_slop=false`, потому что «важно понимать»
в `corpus-10` написал автор. Остальные кейсы — короткие синтетические
предложения под конкретный дефект.

## Без API-ключа

Этот запуск не оценивает качество prompt: identity-provider возвращает исходник.
Он проверяет подключение обоих prompt и детерминированных затворов:

```bash
EDITOR_EVAL_OFFLINE=1 PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.yaml --no-cache \
  --no-table --no-progress-bar -o /tmp/editorteam-promptfoo-offline.json
```

В identity-режиме неотредактированные кейсы с AI-фразами и канцеляритом
обязаны завершиться `fail`: это доказывает, что assertions ловят дефект, а не
ошибку запуска. Поэтому общий exit code будет ненулевым.
Такой результат честно хранит `accepted=null`, `checks_complete=false` и
`deterministic_only=true`: offline-прогон не выдаётся за проверку моделью и
внешними анализаторами.

## Реальное прямое A/B-сравнение

Поднимите EditorTeam, задайте обе модели и запустите:

```bash
EDITOR_EVAL_PROVIDER=openai \
EDITOR_EVAL_API_KEY="$OPENAI_API_KEY" \
EDITOR_EVAL_BASELINE_MODEL="$BASELINE_MODEL" \
EDITOR_EVAL_CANDIDATE_MODEL="$CANDIDATE_MODEL" \
EDITOR_GATEWAY_URL=http://127.0.0.1:8740 \
PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.yaml --no-cache \
  -o /tmp/editorteam-promptfoo-real.json
```

Можно раздельно задать `EDITOR_EVAL_BASELINE_PROVIDER`,
`EDITOR_EVAL_CANDIDATE_PROVIDER`, `EDITOR_EVAL_BASELINE_API_KEY`,
`EDITOR_EVAL_CANDIDATE_API_KEY` и `EDITOR_EVAL_*_BASE_URL`. Для OpenAI-compatible endpoint
используйте `EDITOR_EVAL_PROVIDER=openai-compatible` и `EDITOR_EVAL_BASE_URL`.

Каждый результат сохраняет `provider`, `model`, `prompt_version`, `accepted`,
`checks_complete`, вердикт validation и SHA-256 итога. Если EditorTeam отклонил
кандидата или не завершил все проверки, provider возвращает исходный текст.

## Критерии

`evals/assertions/deterministic.js` обязательно проверяет непустой ответ,
сохранение и отсутствие новых чисел/процентов и URL, отрицания, Markdown,
таблицы, code blocks, игровые сущности, AI-фразы, канцелярит,
`checks_complete` и возврат исходника при отклонении.

Субъективные метрики вынесены в дополнительный
`evals/promptfooconfig.judge.yaml`: восемь `llm-rubric` с `weight: 0` и
именами `judge-clarity`, `judge-naturalness`, `judge-structure`,
`judge-usefulness`, `judge-voice`, `judge-ai-slop`, `judge-bureaucracy`,
`judge-false-positives`. Тексты рубрик лежат в `evals/assertions/judge/*.txt`
и всегда сравнивают ответ с исходником `{{text}}`. Нулевой вес означает, что
Promptfoo записывает балл как именованную метрику, но pass/fail кейса
определяют только детерминированные затворы. LLM-as-a-judge никогда не
заменяет их.

```bash
EDITOR_EVAL_JUDGE_PROVIDER=openai:gpt-4o-mini \
EDITOR_EVAL_PROVIDER=openai EDITOR_EVAL_API_KEY="$OPENAI_API_KEY" \
EDITOR_EVAL_BASELINE_MODEL="$BASELINE_MODEL" EDITOR_EVAL_CANDIDATE_MODEL="$CANDIDATE_MODEL" \
PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.judge.yaml --no-cache \
  -o /tmp/editorteam-promptfoo-judge.json
```

## Candidate без retrieval и с retrieval

`evals/promptfooconfig.retrieval.yaml` гоняет candidate через production
`/v2/edit` дважды: с `retrieval: "off"` в запросе и с включённым подбором
примеров авторского стиля из корпуса. Оба провайдера ходят в один gateway
(`EDITOR_EVAL_CANDIDATE_GATEWAY_URL`). Запускайте только на реальных
фрагментах корпуса:

```bash
EDITOR_EVAL_CANDIDATE_GATEWAY_URL=http://127.0.0.1:8740 \
EDITOR_EVAL_ANALYZER_URL=http://127.0.0.1:8731 \
EDITOR_EVAL_JUDGE_PROVIDER=openai:gpt-4o-mini \
PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.retrieval.yaml --filter-pattern corpus \
  --no-cache -o /tmp/editorteam-retrieval.json
```

Краткий отчёт по JSON-выводу строит `evals/report.js`: число кейсов,
accepted/rejected/unchanged rate, `checks_complete` rate, счётчики
`corpus_copy` и `corpus_fact_leak`, сохранение фактов и Markdown, средний
объём изменений, candidate win rate, разрезы по профилям и играм. Без
результатов настоящей модели (offline-провайдер, пустой или отсутствующий
файл) он печатает `real model evaluation not executed` и ничего не
придумывает. CI проверяет парсер на `evals/fixtures/promptfoo-retrieval-sample.json`;
платный прогон остаётся ручным.

```bash
node evals/report.js /tmp/editorteam-retrieval.json
```

Модели сравниваются переменными `EDITOR_EVAL_BASELINE_MODEL`,
`EDITOR_EVAL_CANDIDATE_MODEL`, `EDITOR_EVAL_PROVIDER` (принимается и
`EDITOR_EVAL_PROVIDER_PROVIDER`) и `EDITOR_EVAL_JUDGE_PROVIDER`; для локальной
модели через Ollama задайте `EDITOR_EVAL_PROVIDER=ollama` и
`EDITOR_EVAL_BASE_URL=http://127.0.0.1:11434/v1`. В pipeline-режиме модель
выбирает gateway, поэтому две модели сравниваются двумя gateway
(`EDITOR_EVAL_BASELINE_GATEWAY_URL`, `EDITOR_EVAL_CANDIDATE_GATEWAY_URL`).
Запрос может задать `retrieval`: `auto` (по умолчанию: включён для edit и
rewrite, выключен для proofread), `on` или `off`.

Детерминированный затвор тот же плюс `no-corpus-copy`: провайдер берёт
тексты примеров у сайдкара (`/corpus/examples`, публичный API их не
отдаёт) и проваливает кейс, если в ответе появился словесный 10-граммный
фрагмент из примера, которого нет в исходнике. Числа, URL и защищённые
сущности из примеров ловят существующие проверки `no-new-*`. Judge-метрики
с нулевым весом: `judge-voice`, `judge-naturalness`, `judge-clarity`,
`judge-usefulness`, `judge-facts`, `judge-example-copying`. В metadata
каждого результата есть `retrieval_variant`, `retrieval_status`,
`retrieval_examples_used` и `retrieval_example_ids`.

## Редакторский набор и порог качества

`evals/cases/editorial.json` — 36 кейсов в каноническом формате (`id`, `game`,
`profile`, `source`, `reference`, `expected_action`, `defects`,
`must_preserve`, `allowed_changes`); собирается командой
`python tools/build_editorial_cases.py`. Кейсы `unchanged` — настоящие
абзацы `гайды/`, которые нельзя переписывать; кейсы `edit` — те же абзацы с
детерминированно внесёнными дефектами (AI-рамки, канцелярит, повторы,
перегруженные предложения, сломанная структура), где reference — авторский
оригинал. WoW и LoL в корпусе нет: их кейсы помечены `synthetic: true`.
Промптфу-загрузчик `evals/cases/editorial.js` переводит набор в переменные
без дублирования данных.

```bash
EDITOR_EVAL_CANDIDATE_GATEWAY_URL=http://127.0.0.1:8740 \
EDITOR_EVAL_ANALYZER_URL=http://127.0.0.1:8731 \
EDITOR_EVAL_JUDGE_PROVIDER=openai:gpt-4o-mini \
PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.editorial.yaml --no-cache \
  -o /tmp/editorteam-editorial.json
node evals/report.js /tmp/editorteam-editorial.json
```

`evals/report.js` печатает по каждому варианту accepted/rejected/unchanged
rate, `checks_complete` rate, счётчики `corpus_copy` и `corpus_fact_leak`,
сохранение фактов и Markdown, объём изменений по token-level edit distance с
учётом перенесённых предложений и переставленных слов, переходы
`edit → unchanged` и `unchanged → edit`, средние judge-оценки по метрикам,
разрезы по профилям и играм, полноту A/B-пар с перечнем отсутствующих и
дублирующихся, win/loss rate candidate и итоговый verdict `pass|fail`
по порогам `evals/thresholds.json`. При провале порогов exit code равен 1.
Пороги применяются только к настоящему прогону: для offline-, fake- и
fixture-результатов скрипт печатает `real model evaluation not executed`
и завершается нулём.

## Слепая человеческая оценка

```bash
node evals/blind_review.js prepare /tmp/editorteam-editorial.json --out build/blind
# оценщику отдать build/blind/pairs.md и ratings.template.json; key.json хранить отдельно
node evals/blind_review.js import build/blind/ratings.json --key build/blind/key.json --out build/blind
```

`prepare` случайно (криптографический источник) скрывает, какой вариант
стал A, а какой B, и пишет ключ рандомизации в отдельный `key.json`.
Оценщик ставит 1–5 за читаемость, естественность русского, полезность для
игрока и сохранение авторского голоса и указывает предпочтение. `import`
раскрывает ключ и строит `report.json` и `report.md` со средними по
вариантам и распределением предпочтений.

## Production A/B через два gateway

Запустите два gateway с одинаковой моделью и анализаторами, но с разными
`EDITOR_PROMPT_VARIANT`, затем выполните ручной внешний прогон:

```bash
EDITOR_EVAL_BASELINE_GATEWAY_URL=http://127.0.0.1:8740 \
EDITOR_EVAL_CANDIDATE_GATEWAY_URL=http://127.0.0.1:8741 \
PROMPTFOO_DISABLE_TELEMETRY=1 \
  npx promptfoo eval -c evals/promptfooconfig.pipeline.yaml --no-cache \
  -o /tmp/editorteam-pipeline-real.json
```

CI не использует платный API: локальный OpenAI-compatible fake проверяет
фактические вызовы модели, последовательность стадий, repair и защитный отказ.
Внешний прогон оставлен ручным, поскольку он требует выбранных владельцем
моделей и ключей.

Новый кейс добавляется объектом в `evals/cases/cases.json` с `vars.id`, `vars.text`,
`vars.game`, `vars.profile`, `vars.protected_entities` и `vars.expected_properties`.
Фрагменты корпуса дополнительно получают `vars.origin`, каждое защищённое
название должно встречаться в тексте дословно, ссылки и ники не допускаются.
Не добавляйте имена реальных авторов или приватные материалы.

## Диагностика

```bash
node --test evals/tests/evals.test.js
npx promptfoo validate -c evals/promptfooconfig.yaml
npx promptfoo validate -c evals/promptfooconfig.pipeline.yaml
npx promptfoo validate -c evals/promptfooconfig.judge.yaml
python3 -c "import json;d=json.load(open('evals/cases/cases.json'));print(len(d))"
npx promptfoo view   # таблица baseline/candidate по последнему прогону
```

В JSON-выводе каждого прогона поле `results[].response.metadata` содержит
`provider`, `model`, `prompt_version`, `accepted`, `checks_complete`; в offline-режиме
там же стоит `deterministic_only=true`.
