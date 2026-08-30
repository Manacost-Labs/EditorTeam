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
    -o /out/editor-gateway ./cmd/editor-gateway

FROM alpine:3.22 AS gateway

RUN apk add --no-cache ca-certificates \
    && addgroup -S editor \
    && adduser -S -G editor editor
COPY --from=gateway-build /out/editor-gateway /usr/local/bin/editor-gateway

USER editor
WORKDIR /app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=6 \
  CMD wget -qO- http://127.0.0.1:8080/health >/dev/null

ENTRYPOINT ["/usr/local/bin/editor-gateway"]
