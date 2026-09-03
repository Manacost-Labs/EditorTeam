# Evaluation через Promptfoo

Основной конфиг `evals/promptfooconfig.yaml` сравнивает два полных
системных prompt: `baseline` воспроизводит бережную редактуру до
нового pipeline, `candidate` добавляет практическую пользу, защиту
авторского голоса и удаление AI-шаблонов. Provider принимает только
точное содержимое этих двух файлов. Произвольный system prompt
отклоняется до вызова модели; production API не принимает system prompt.

В наборе 42 обезличенных кейса для гайдов, новостей, мета-отчётов,
аналитики, Hearthstone, World of Warcraft и League of Legends. Он включает
длинные обезличенные фрагменты, сленг, числа, URL, ссылки, таблицы,
списки и fenced code blocks.

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

## Реальное A/B-сравнение

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

Субъективные метрики — ясность, естественность русского языка, структура,
полезность, голос, нейрослоп, канцелярит и ложные срабатывания — вынесены в
дополнительный `evals/promptfooconfig.judge.yaml`. Затворы остаются основными;
LLM-as-a-judge никогда не заменяет их.

Новый кейс добавляется объектом в `evals/cases/cases.json` с `vars.id`, `vars.text`,
`vars.game`, `vars.profile`, `vars.protected_entities` и `vars.expected_properties`.
Не добавляйте имена реальных авторов или приватные материалы.
