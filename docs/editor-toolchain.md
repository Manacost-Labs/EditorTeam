# Редакторский toolchain

EditorTeam использует Go как оркестратор. Он принимает текст, собирает
компактный RuleBundle, вызывает модель, а затем повторяет проверки и не
принимает результат с изменёнными защищёнными данными.

Проверки разделены по назначению:

- существующие Python-анализаторы EditorTeam — голос, структура, карточные
  названия, semantic diff и rewrite gate;
- Natasha + Razdel — границы предложений и токенов, морфология, сущности,
  повторы и слишком длинные предложения;
- Hunspell — только подсказки о возможных опечатках и неизвестных словах;
- LanguageTool — пунктуация и общеязыковые правила через `/v2/check`;
- Vale — мягкие редакторские сигналы в Markdown;
- markdownlint — структура Markdown;
- Native Go — обязательные термины «Темные дары» и «тип существа».

Внешние проверки ничего не исправляют. Модель получает findings, а итог
проходит сравнение ссылок, чисел, сущностей, отрицаний, осторожных условий и
разметки. Невозможность запуска инструмента возвращается как
`rule_id=analyzer_unavailable`, поэтому отсутствие проверки видно клиенту.
Razdel fallback аналогично помечается `analyzer_degraded` и никогда
не поднимает `checks_complete=true`.

## Запуск

```bash
docker compose config
docker compose build
docker compose up -d --wait
curl http://127.0.0.1:8740/health
curl -X POST http://127.0.0.1:8740/v2/edit \
  -H 'content-type: application/json' \
  -d '{"text":"Поля сражений: 42% побед","mode":"edit","game":"hearthstone","profile":"battlegrounds-article"}'
docker compose down
```

Для локального запуска без Docker поднимите существующий Python-сайдкар,
`sidecars/nlp/server.py`, LanguageTool Server и установите `vale`, `hunspell`,
`markdownlint-cli2`. Адреса и таймауты задаются только переменными окружения.

## Игровой термин

Добавьте термин отдельной строкой в соответствующий файл
`config/dictionaries/*.txt`. Это allowlist для Hunspell, а не команда модели
заменять слово. Спорные варианты не добавляйте: их должен решить редактор.
В Docker словарь всегда доступен по пути
`/usr/share/hunspell/ru_RU.dic`; его версия и хеш закреплены в `Dockerfile`.
