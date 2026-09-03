FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
EXPOSE 8765
HEALTHCHECK --interval=2s --timeout=2s --start-period=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=1)"
USER nobody
CMD ["python", "server.py"]
