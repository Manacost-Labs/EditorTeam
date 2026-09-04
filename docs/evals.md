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

Детерминированный затвор тот же плюс `no-corpus-copy`: провайдер берёт
тексты примеров у сайдкара (`/corpus/examples`, публичный API их не
отдаёт) и проваливает кейс, если в ответе появился словесный 10-граммный
фрагмент из примера, которого нет в исходнике. Числа, URL и защищённые
сущности из примеров ловят существующие проверки `no-new-*`. Judge-метрики
с нулевым весом: `judge-voice`, `judge-naturalness`, `judge-clarity`,
`judge-usefulness`, `judge-facts`, `judge-example-copying`. В metadata
каждого результата есть `retrieval_variant`, `retrieval_status`,
`retrieval_examples_used` и `retrieval_example_ids`.

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
