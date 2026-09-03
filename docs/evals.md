# Evaluation через Promptfoo

В `evals/` лежат два prompt-варианта, локальный провайдер EditorTeam,
детерминированные assertions и 30 обезличенных кейсов: гайды, новости,
мета-отчёты, аналитика, короткие и длинные тексты, сленг, таблицы, списки,
ссылки, числа и Markdown code blocks.

Запуск:

```bash
npx promptfoo eval
# либо явно: npx promptfoo eval -c evals/promptfooconfig.yaml
```

Провайдер не хранит модель и ключи в репозитории. Он использует
`EDITOR_GATEWAY_URL`, а сам EditorTeam читает `EDITOR_PROVIDER`,
`EDITOR_MODEL`, `OPENAI_API_KEY`, `OLLAMA_BASE_URL` и остальные настройки из
окружения. Для локальной модели можно выбрать `EDITOR_PROVIDER=ollama` без
ключа; Go использует OpenAI-совместимый endpoint Ollama. Для baseline задайте `EDITOR_EVAL_MODE=baseline`, для нового
редакторского режима — `EDITOR_EVAL_MODE=candidate`.

Встроенные assertions проверяют, что ответ не пустой и что Go сохранил числа,
ссылки и другие защищённые фрагменты. Остальные показатели (грамотность,
естественность, голос, канцелярит, нейрослоп, ложные срабатывания) снимаются
из findings и требуют просмотра выборки редактором; LLM-as-a-judge не является
единственным критерием.

Новый кейс добавляется объектом в `evals/cases/cases.json` с полями `id`,
`text`, `game`, `profile` и `expected_properties`. Не добавляйте в кейсы имена
реальных авторов или приватные материалы.
