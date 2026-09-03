# Сторонние компоненты

Компоненты подключаются как отдельные инструменты или необязательные
сайдкары. Перед выпуском образа проверьте актуальные тексты лицензий и версии.

| Компонент | Назначение | Лицензия | Ссылка |
| --- | --- | --- | --- |
| ru-text | каталоги «False intensifiers», «Канцелярит» и «Passive voice» адаптированы в `.vale/styles/EditorTeam/{Intensifiers,Wordiness,PassiveVoice}.yml`; тексты правил написаны заново, оставлены только обороты, отсутствующие в авторском корпусе | MIT, текст в `third_party/licenses/ru-text-LICENSE` | https://github.com/talkstream/ru-text |
| Natasha | морфология, леммы и NER | MIT | https://github.com/natasha/natasha |
| Razdel | разбиение русского текста | MIT | https://github.com/natasha/razdel |
| Hunspell | подсказки орфографии; в образ ставится пакет Alpine `hunspell`, слова никогда не исправляются автоматически | LGPL-2.1 / GPL-2.0 / MPL-1.1 (tri-license) | https://github.com/hunspell/hunspell |
| ru-spelling-dictionary | русский словарь Hunspell `ru_RU.aff`/`ru_RU.dic`, релиз v1.0.8 | MPL-2.0; текст в `third_party/licenses/ru-spelling-dictionary-LICENSE` и в образе `/usr/share/licenses/ru-spelling-dictionary/LICENSE` | https://github.com/Goudron/ru-spelling-dictionary |
| markdownlint | проверка Markdown | MIT | https://github.com/DavidAnson/markdownlint |
| Vale | мягкие стилевые правила, бинарник 3.17.0 в образе | MIT | https://github.com/vale-cli/vale |
| LanguageTool | общеязыковая проверка | LGPL | https://languagetool.org/ |
| Promptfoo | сравнение prompt и evaluation | MIT | https://github.com/promptfoo/promptfoo |

## Уже используемые зависимости

| Компонент | Лицензия | Зачем |
|---|---|---|
| [pymorphy3](https://github.com/no-plagiarism/pymorphy3) | MIT | морфологический разбор русского |
| pymorphy3-dicts-ru | MIT | словари к нему |
| [PyYAML](https://pyyaml.org/) | MIT | чтение конфигурации |
| [pytest](https://pytest.org/) | MIT | тесты |
| [ruff](https://github.com/astral-sh/ruff) | MIT | линтер и форматтер |

## Данные

**[HearthstoneJSON](https://hearthstonejson.com)** — справочник карт,
русская локализация. Названия карт, классов и механик Hearthstone
принадлежат Blizzard Entertainment. Проект не связан с Blizzard и не
одобрен ею.

## Идеи каталога маркеров

Механика уровней действия и часть категорий переработаны из открытых
проектов; тексты правил написаны заново под русский язык.

- [better-writing](https://github.com/jpcaparas/skills) — три уровня
  действия, правило «синонимом не лечится»
- [humanizer](https://github.com/blader/humanizer) — каталог признаков
  машинного текста
- [author-toolkit](https://github.com/rhavekost/author-toolkit) — аудиты
  ритма и повторов, принцип «аудит выдаёт кандидатов, а не правки»
- [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism)
  — идея листа стиля и фильтр четырёх вопросов

Модели и API-ключи в репозитории не хранятся. Словарь ru_RU
скачивается только при сборке Docker-образа и проверяется по SHA-256.

## Закреплённые Docker-артефакты

| Артефакт | Версия | SHA-256 | Лицензия |
| --- | --- | --- | --- |
| `ru-spelling-dictionary-hunspell-1.0.8.zip` | 1.0.8 | `b3a4672933b957258be74c6c46e016c83e8e9c796259a08c00f8fd52ebed2d97` | MPL-2.0; `LICENSE` копируется в образ |
| `vale_3.17.0_Linux_64-bit.tar.gz` | 3.17.0 | `a903f1f60c3293fac643e0137f599a462881cc691ee19d6120dcfc786f1be86d` | MIT |
| `vale_3.17.0_Linux_arm64.tar.gz` | 3.17.0 | `c7da52f10d25fb97e14370b2f77ac5ebdbd23cf0abc156659463cfa785282692` | MIT |

## Скиллы разработки проекта

Проектные скиллы в `.agents/skills/` импортированы из
[`Manacost-Labs/skills`](https://github.com/Manacost-Labs/skills) на ревизии
`dec9878a1f3d12a7c0a2a5b1419b146f07f2c847`.

| Источник | Что импортировано | Лицензия |
| --- | --- | --- |
| Manacost-Labs/skills | проектный профиль и полный `AGENTS.md` | provenance центрального каталога; лицензии отдельных скиллов указаны ниже |
| addyosmani/agent-skills | API, CI/CD, контекст, документация, Git, производительность, планирование, source-driven development, выбор скиллов | MIT |
| mattpocock/skills | codebase design, domain modeling, diagnosing bugs | MIT |
| nathankim0/clean-architecture-skills | Clean Architecture | MIT |
| канонические data-скиллы Manacost Labs | Karpathy discipline, ревью, Python quality, TDD, debugging, verification | metadata лицензии сохранена в `SKILL.md`, если она была в источнике |

Дословные тексты доступных сторонних лицензий находятся в
`third_party/licenses/`.
