#!/bin/sh
set -eu

image="${1:-editorteam-gateway:test}"

docker run --rm --entrypoint sh "$image" -ec '
  test "$(id -u)" != "0"
  test -r "$RU_DICT_PATH"
  test -r "$VALE_CONFIG"
  test -r /app/.vale/styles/EditorTeam/Overcertainty.yml
  vale --version | grep -F "vale version 3.17.0"

  # Hunspell: a real Russian typo is reported with a suggestion, never fixed.
  hunspell_output=$(printf "сабака\n" | hunspell -a -d "${RU_DICT_PATH%.dic}")
  printf "%s\n" "$hunspell_output" | grep -E "^& сабака .*: .*собака"

  # Vale: the EditorTeam rule fires as a suggestion in the news profile...
  printf "%s\n" "Этот вариант гарантированно побеждает любую колоду." > /tmp/test.news.md
  vale_output=$(vale --config="$VALE_CONFIG" --output=JSON /tmp/test.news.md || test "$?" = "1")
  printf "%s\n" "$vale_output" | grep -F "EditorTeam.Overcertainty"
  printf "%s\n" "$vale_output" | grep -F "\"Severity\": \"suggestion\""

  # ...and stays silent for the same sentence inside a guide.
  cp /tmp/test.news.md /tmp/test.guide.md
  guide_output=$(vale --config="$VALE_CONFIG" --output=JSON /tmp/test.guide.md || test "$?" = "1")
  if printf "%s\n" "$guide_output" | grep -qF "EditorTeam.Overcertainty"; then
    echo "guide profile must not flag Overcertainty" >&2
    exit 1
  fi
'
