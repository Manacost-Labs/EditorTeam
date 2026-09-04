# The analyzer keeps the repository's Python rules and corpus beside the installed dependencies.
FROM python:3.12-slim AS analyzer

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
COPY src ./src
COPY .claude ./.claude
COPY config ./config
COPY гайды ./гайды

RUN python -m pip install --no-cache-dir \
    'pymorphy3>=2.0' \
    'pymorphy3-dicts-ru>=2.4' \
    'PyYAML>=6.0'

EXPOSE 8731
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8731/health', timeout=3)"

CMD ["python", "-m", "editorteam.server", "--host", "0.0.0.0", "--port", "8731"]

# The gateway is a small static Go binary; the runtime image contains only CA roots and that binary.
FROM golang:1.26.4-alpine AS gateway-build

WORKDIR /src
COPY go ./go
RUN cd go && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' \
    -o /out/editor-gateway ./cmd/editor-gateway && \
    CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' \
    -o /out/editorteam ./cmd/editorteam

FROM alpine:3.22 AS editorial-tools

ARG TARGETARCH
ARG RU_DICT_VERSION=1.0.8
ARG RU_DICT_SHA256=b3a4672933b957258be74c6c46e016c83e8e9c796259a08c00f8fd52ebed2d97
ARG VALE_VERSION=3.17.0
ARG VALE_AMD64_SHA256=a903f1f60c3293fac643e0137f599a462881cc691ee19d6120dcfc786f1be86d
ARG VALE_ARM64_SHA256=c7da52f10d25fb97e14370b2f77ac5ebdbd23cf0abc156659463cfa785282692

RUN apk add --no-cache ca-certificates curl gcompat libstdc++ tar unzip \
    && set -eux; \
    arch="${TARGETARCH:-$(apk --print-arch)}"; \
    case "$arch" in \
      "amd64"|"x86_64") vale_asset="vale_${VALE_VERSION}_Linux_64-bit.tar.gz"; vale_sha="$VALE_AMD64_SHA256" ;; \
      "arm64"|"aarch64") vale_asset="vale_${VALE_VERSION}_Linux_arm64.tar.gz"; vale_sha="$VALE_ARM64_SHA256" ;; \
      *) echo "unsupported target architecture: $arch" >&2; exit 1 ;; \
    esac; \
    dict_asset="ru-spelling-dictionary-hunspell-${RU_DICT_VERSION}.zip"; \
    curl -fsSLo /tmp/dictionary.zip \
      "https://github.com/Goudron/ru-spelling-dictionary/releases/download/v${RU_DICT_VERSION}/${dict_asset}"; \
    echo "${RU_DICT_SHA256}  /tmp/dictionary.zip" | sha256sum -c -; \
    mkdir -p /out/hunspell /out/licenses/ru-spelling-dictionary; \
    unzip -q /tmp/dictionary.zip -d /tmp/dictionary; \
    cp /tmp/dictionary/ru_RU.aff /tmp/dictionary/ru_RU.dic /out/hunspell/; \
    cp /tmp/dictionary/LICENSE /out/licenses/ru-spelling-dictionary/LICENSE; \
    curl -fsSLo /tmp/vale.tar.gz \
      "https://github.com/vale-cli/vale/releases/download/v${VALE_VERSION}/${vale_asset}"; \
    echo "${vale_sha}  /tmp/vale.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/vale.tar.gz -C /tmp; \
    cp /tmp/vale /out/vale; \
    chmod 0755 /out/vale; \
    /out/vale --version

FROM gateway-build AS gateway-integration

RUN apk add --no-cache hunspell gcompat libstdc++
COPY --from=editorial-tools /out/hunspell/ru_RU.aff /usr/share/hunspell/ru_RU.aff
COPY --from=editorial-tools /out/hunspell/ru_RU.dic /usr/share/hunspell/ru_RU.dic
COPY --from=editorial-tools /out/vale /usr/local/bin/vale
COPY .vale.ini /src/.vale.ini
COPY .vale /src/.vale
RUN cd go && HUNSPELL_INTEGRATION=1 HUNSPELL_BIN=hunspell \
    RU_DICT_PATH=/usr/share/hunspell/ru_RU.dic \
    go test ./internal/hunspell -run TestRealRussianDictionaryDetectsTypoAndKeepsGameAllowlist -count=1 \
    && VALE_BIN=/usr/local/bin/vale VALE_CONFIG=/src/.vale.ini \
    go test ./internal/analyzers -run TestValeAdapterAppliesProfileSectionsWithRealBinary -count=1 -v

FROM alpine:3.22 AS gateway

RUN apk add --no-cache ca-certificates gcompat libstdc++ nodejs npm hunspell \
    && npm install --global markdownlint-cli2@0.17.2 \
    && addgroup -S editor \
    && adduser -S -G editor editor
COPY --from=gateway-build /out/editor-gateway /usr/local/bin/editor-gateway
COPY --from=gateway-build /out/editorteam /usr/local/bin/editorteam
COPY --from=editorial-tools /out/vale /usr/local/bin/vale
COPY --from=editorial-tools /out/hunspell/ru_RU.aff /usr/share/hunspell/ru_RU.aff
COPY --from=editorial-tools /out/hunspell/ru_RU.dic /usr/share/hunspell/ru_RU.dic
COPY --from=editorial-tools /out/licenses/ru-spelling-dictionary/LICENSE /usr/share/licenses/ru-spelling-dictionary/LICENSE
COPY --chown=editor:editor .vale.ini /app/.vale.ini
COPY --chown=editor:editor .vale /app/.vale
COPY config /app/config
COPY evals /app/evals

ENV RU_DICT_PATH=/usr/share/hunspell/ru_RU.dic \
    VALE_BIN=/usr/local/bin/vale \
    VALE_CONFIG=/app/.vale.ini

RUN vale --version \
    && test -r /app/.vale.ini \
    && test -r /app/.vale/styles/EditorTeam/Overcertainty.yml \
    && test -r "$RU_DICT_PATH"

USER editor
WORKDIR /app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=6 \
  CMD wget -qO- http://127.0.0.1:8080/health >/dev/null

ENTRYPOINT ["/usr/local/bin/editorteam"]
