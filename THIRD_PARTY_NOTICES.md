# Сторонние компоненты

Компоненты подключаются как отдельные инструменты или необязательные
сайдкары. Перед выпуском образа проверьте актуальные тексты лицензий и версии.

| Компонент | Назначение | Лицензия | Ссылка |
| --- | --- | --- | --- |
| ru-text | идеи правил против нейрослопа и канцелярита; правила адаптируются локально | MIT | https://github.com/talkstream/ru-text |
| Natasha | морфология, леммы и NER | MIT | https://github.com/natasha/natasha |
| Razdel | разбиение русского текста | MIT | https://github.com/natasha/razdel |
| Hunspell | подсказки орфографии | LGPL/MPL/GPL | https://github.com/hunspell/hunspell |
| ru-spelling-dictionary | русский словарь Hunspell | MPL-2.0 | https://github.com/Goudron/ru-spelling-dictionary |
| markdownlint | проверка Markdown | MIT | https://github.com/DavidAnson/markdownlint |
| Vale | мягкие стилевые правила | MIT | https://vale.sh/ |
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

В репозитории не хранятся словарные базы, модели и API-ключи. Они должны
поставляться отдельно и проверяться по лицензии в конкретном дистрибутиве.
