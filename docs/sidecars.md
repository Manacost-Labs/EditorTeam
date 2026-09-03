# Сайдкары и внешние проверки

## Natasha/Razdel

Сайдкар находится в `sidecars/nlp`. Воспроизводимая установка: `pip install
--require-hashes -r sidecars/nlp/requirements.lock`, запуск: `python sidecars/nlp/server.py --port
8742`. Он принимает `POST /analyze` с `text`, `language`, `game`, `profile` и
возвращает `sentences`, `tokens`, `paragraphs`, `entities`, `terms`,
`findings`, `meta`. `GET /health` показывает, загружены ли обе библиотеки.
Результаты кэшируются по SHA-256 версии NLP, языка, игры, профиля и текста.
Размер одного запроса — 2 МБ.
Один анализ ограничен десятью секундами и четырьмя рабочими потоками.

Если Natasha не установлена, Razdel и базовый разбор продолжают работать, но
`meta.complete=false`. Go сохраняет fallback-findings, добавляет
`{analyzer:"natasha-razdel",rule_id:"analyzer_degraded",severity:"info"}` и
возвращает `checks_complete=false`. Невалидный JSON, HTTP 500 и таймаут
также не маскируются под здоровый сайдкар.

Production закрепляет Natasha 1.6.0, Razdel 0.5.0 и setuptools 80.10.2 с
хешами всех транзитивных пакетов. Lock проверяется на Python 3.12 и 3.13.
Внешние offsets формирует Razdel; `offset/length` измеряются символами,
`byte_offset/byte_length` — UTF-8 байтами, `line/column` начинаются с единицы.

## LanguageTool

Go отправляет URL-кодированную форму на `LANGUAGETOOL_URL/v2/check`, всегда с
явным языком (`ru-RU`, `en-US` или `pl-PL`). Ответы преобразуются в общий
Finding; исправление текста выполняет только модель после проверки редактора.
Compose использует закреплённый образ `erikvl87/languagetool:6.8` с digest
`sha256:ef8fa12cbd485166c9ceeb7139d76d56d07707a624da6bb1fc1fbb5411750527`.
Healthcheck делает настоящий русский запрос к `/v2/check`.

## Vale

Vale запускается на временном файле с правами 0600, JSON читается без shell,
процесс ограничен таймаутом и размером вывода. Конфигурация `.vale.ini` и
правила `.vale/styles/EditorTeam/*.yml` намеренно имеют мягкую серьёзность:
они подсказывают убрать шаблонные вводные, рекламный тон и повторы, но не
ломают разговорный голос игровых статей.
Vale 3.17.0 устанавливается в Docker для `amd64` и `arm64` с проверкой
SHA-256. Имя временного файла выбирает профил: `guide`, `news`,
`analysis` или `meta-report`. Цитаты, code blocks, inline-code, URL и заголовки
исключаются из контекстных сигналов.

## Hunspell

Укажите `HUNSPELL_BIN` и `RU_DICT_PATH` (путь к каталогу или `ru_RU.dic`).
Перед запуском URL, Markdown, deck code и игровые сущности маскируются. Список
исключений хранится в `config/dictionaries/`. Результат Hunspell — finding, не
автоматическая замена.

Образ закрепляет ru-spelling-dictionary 1.0.8 по SHA-256 и копирует
`ru_RU.aff`, `ru_RU.dic` и текст MPL-2.0. Рабочий Docker-путь:
`/usr/share/hunspell/ru_RU.dic`.

## markdownlint

По умолчанию используется `markdownlint-cli2` и конфигурация
`config/markdownlint/.markdownlint.json`. Правила MD013 и MD041 отключены,
поскольку длинные строки и отсутствие H1 встречаются в текущих игровых
материалах. Запуск вручную: `markdownlint-cli2 --config
config/markdownlint/.markdownlint.json article.md`.
