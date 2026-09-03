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

В health поле `natasha` отдельно показывает `status` (`ok`, `degraded` или
`unavailable`), `complete`, engine и версию NLP. Любой обязательный
анализатор не в полном состоянии переводит общий `checks_complete` в `false`.

## Диагностика

```bash
docker compose ps
docker compose logs --no-color --tail=200 gateway nlp analyzer languagetool
curl --fail -s http://127.0.0.1:8740/health | python3 -m json.tool
curl --fail -s http://127.0.0.1:8742/health | python3 -m json.tool        # Natasha: ok и complete
curl --fail -s --data 'language=ru-RU&text=Простой+тест' http://127.0.0.1:8010/v2/check >/dev/null
docker compose exec gateway vale --version
docker compose exec gateway vale --config=/app/.vale.ini /app/evals/cases/cases.json 2>/dev/null; true
docker compose exec gateway sh -c 'printf "сабака\n" | hunspell -a -d "${RU_DICT_PATH%.dic}"'
docker compose exec gateway markdownlint-cli2 --version
docker build --target gateway --tag editorteam-gateway:test . && tests/integration/docker_toolchain.sh editorteam-gateway:test
docker buildx build --platform linux/amd64,linux/arm64 --target gateway-integration .   # настоящий словарь и опечатка
python tests/integration/nlp_sidecar_http.py
python tests/integration/pipeline_e2e.py     # нужен docker-compose.e2e.yml с fake-openai
```

Что должно быть в ответе `/health` при стандартном запуске: `ok=true`,
`checks_complete=true`, в `analyzers` все семь значений `ok`
(`native-go`, `python`, `natasha-razdel`, `hunspell`, `languagetool`, `vale`,
`markdownlint`), `natasha.status=ok` и `natasha.complete=true`. Если один из
инструментов остановить, его имя останется в `analyzers` со статусом
`unavailable`, а `checks_complete` станет `false`; `/v2/edit` тогда вернёт
исходный текст с `rule_id=analyzer_unavailable`.

Для локального запуска без Docker поднимите существующий Python-сайдкар,
`sidecars/nlp/server.py`, LanguageTool Server и установите `vale`, `hunspell`,
`markdownlint-cli2`. Адреса и таймауты задаются только переменными окружения.

## Игровой термин

Добавьте термин отдельной строкой в соответствующий файл
`config/dictionaries/*.txt`. Это allowlist для Hunspell, а не команда модели
заменять слово. Спорные варианты не добавляйте: их должен решить редактор.
В Docker словарь всегда доступен по пути
`/usr/share/hunspell/ru_RU.dic`; его версия и хеш закреплены в `Dockerfile`.
