# Повторная доставка события

Если обработчик не ответил, система может повторить запрос. Проверить состояние можно командой `curl -fsS http://localhost:8787/healthz`. Сохраняйте без изменений `idempotency_key`, путь `/v1/events/retry`, заголовок `X-Request-ID` и значение `MAX_RETRIES=3`.

```yaml
retry:
  max_attempts: 3
  backoff: exponential
```

Мы не тестировали этот сценарий при потере связи дольше 15 минут.
