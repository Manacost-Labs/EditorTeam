#!/bin/sh
set -eu

image="${1:-editorteam-gateway:test}"

docker run --rm --entrypoint sh "$image" -ec '
  test "$(id -u)" != "0"
  test -r "$RU_DICT_PATH"
  test -r "$VALE_CONFIG"
  vale --version | grep -F "vale version 3.17.0"

  hunspell_output=$(printf "сабака\n" | hunspell -a -d "${RU_DICT_PATH%.dic}")
  printf "%s\n" "$hunspell_output" | grep -E "^& сабака .*: .*собака"

  printf "%s\n" "Этот вариант гарантированно побеждает любую колоду." > /tmp/test.news.md
  vale_output=$(vale --config="$VALE_CONFIG" --output=JSON /tmp/test.news.md || test "$?" = "1")
  printf "%s\n" "$vale_output" | grep -F "EditorTeam.Overcertainty"
'
