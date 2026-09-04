# Миграция редакторского backend на Go

## Что изменилось

`go/cmd/editorteam` теперь является основным HTTP-оркестратором. Старый
`editor-gateway` и Python-сайдкар остаются рабочими: Go проксирует их
совместимые endpoints и использует Python-проверки как adapter до переноса
морфологии.

Новый staged pipeline доступен по `POST /v2/edit`:

1. извлечение защищенных сущностей;
2. editorial analysis без переписывания;
3. rewrite/edit;
4. critic и не более двух targeted repair;
5. Natasha/Razdel, Hunspell, LanguageTool, Vale, markdownlint и Python
   semantic validators;
6. повтор всех проверок и финальный guard.

## Пример запроса

```json
{
  "text": "В этой статье мы разберем колоду. Она может хорошо играть.",
  "mode": "edit",
  "game": "hearthstone",
  "profile": "analytics-article",
  "language": "ru-RU",
  "editorial_mode": "GUIDE"
}
```

## Пример ответа

```json
{
  "text": "Разберем колоду. Она может хорошо играть.",
  "mode": "edit",
  "changes": [{"line": 1, "before": "...", "after": "..."}],
  "factual_risks": [],
  "qa_findings": [],
  "protected_entities_changed": [],
  "scores": {
    "factual_preservation": 9,
    "meaning_preservation": 9,
    "clarity": 8,
    "structure": 8,
    "usefulness": 8,
    "natural_russian": 8,
    "author_voice": 8,
    "terminology": 9
  },
  "scores_valid": true,
  "critic_verdict": "accept",
  "accepted": true,
  "attempts": 2,
  "checks_complete": true,
  "provider": "openai",
  "model": "gpt-4o-mini",
  "prompt_version": "editorteam-go-v2"
}
```

Поле `retrieval` показывает подбор примеров авторского стиля из корпуса:
`status` (`ok`, `unavailable`, `disabled`), `examples_used`, `example_ids`,
`duration_ms`. Тексты примеров в ответ не попадают; модель получает их
отдельным полем `style_examples` и обязана брать из них только форму.
Число, ссылка или название из примера, появившиеся в кандидате без
исходника, отклоняют результат с `corpus_fact_leak`. Дословное совпадение
10–13 слов подряд со стилевым примером — warning `corpus_copy`, 14 и больше
или два совпадения из одного примера — error, который уходит в repair и
после двух неудачных циклов возвращает исходник. Режим retrieval задаёт
запрос полем `retrieval`: `auto` (edit и rewrite — включён, proofread —
`disabled_by_mode`), `on`, `off` (`disabled_by_request`); неизвестное
значение — HTTP 400. Сервис задаёт `EDITOR_RETRIEVAL=auto|on|off`
(`disabled_by_config`). Улучшения critic принимаются только с дословными
цитатами `before` из исходника и `after` из кандидата; без доказанного
улучшения изменённый текст возвращается как `unchanged`.

Отклонённый результат возвращает исходный `text` без `changes`,
`scores_valid=false`, если critic не дал валидный JSON, и список
`rejection_reasons`: `critic_invalid_response`, `critic_rejected`,
`checks_incomplete`, `protected_entity_changed`, `hard_finding`,
`repair_exhausted`. Порядок стадий: preflight исходника → draft →
postflight кандидата → source-aware critic (source, candidate, diff и
tool findings JSON-ом в user message) → объединение findings → targeted
repair (не больше двух) → повторный postflight → повторный critic → final
guards → acceptance. Невалидный JSON critic повторяется один раз с текстом
ошибки; второй сбой отклоняет кандидата без HTTP 502.

## Локальный запуск

1. Запустить Python-сайдкар: `python3 -m editorteam.server --host 127.0.0.1 --port 8731`.
2. Установить `EDITOR_PROVIDER=none` для dry-run или указать провайдера и
   `EDITOR_API_KEY`.
3. При необходимости указать `NATASHA_URL`, `LANGUAGETOOL_URL`, `HUNSPELL_BIN`,
   `RU_DICT_PATH`, `MARKDOWNLINT_BIN`, `VALE_BIN`, `VALE_CONFIG`.
   Для локальной модели без ключа используйте `EDITOR_PROVIDER=ollama`,
   `OLLAMA_BASE_URL` и `EDITOR_MODEL`.
4. Запустить `go run ./cmd/editorteam` из каталога `go`.
5. Проверить `GET http://127.0.0.1:8080/health` и отправить JSON на `/v2/edit`.

В Docker пути уже закреплены: `RU_DICT_PATH=/usr/share/hunspell/ru_RU.dic`
и `VALE_CONFIG=/app/.vale.ini`. Словарь ru_RU и Vale скачиваются только
во время сборки по закреплённым версиям и SHA-256.

Старые клиенты продолжают использовать `/edit`, `/audit`, `/ag-ui` и пять
совместимых endpoint'ов `/health`, `/analyze`, `/validate`, `/rules`,
`/outline/validate`.

## Известные ограничения

- Морфологические и часть semantic-проверок пока выполняются Python-сайдкаром.
- Razdel fallback без Natasha возвращает полезный неполный разбор,
  но обязательно даёт `analyzer_degraded`, `checks_complete=false` и
  не может вести к `accepted=true`.
- При пустом `LANGUAGETOOL_URL` LanguageTool помечается как skipped.
- Если внешний анализатор отсутствует, результат содержит
  `rule_id=analyzer_unavailable` и `checks_complete=false`; по умолчанию такой
  результат не принимается. Для контролируемого dry-run можно явно задать
  `EDITOR_ALLOW_UNAVAILABLE=true`.
- `mode=edit` и `mode=rewrite` требуют настроенного LLM; `EDITOR_PROVIDER=none`
  используется для безопасного запуска и возвращает исходный текст.
- Сохранение в CMS/Google Docs намеренно не выполняется автоматически.
- Golden-набор подключается отдельным этапом после накопления 20–30 согласованных
  результатов; текущие Python golden-тесты не удаляются.
- Vale-правила категоричности (`Overcertainty`, `Promotion`, `Intensifiers`)
  выключены для профиля `guide` и работают как `suggestion` в `news`,
  `analysis` и `meta-report`; ни одно правило стиля не выше `suggestion`.

Команды запуска и диагностики стека собраны в `docs/editor-toolchain.md`,
evaluation — в `docs/evals.md`.
