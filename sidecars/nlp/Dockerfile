FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
  && python -c "from natasha import MorphVocab; MorphVocab()"
COPY server.py ./
EXPOSE 8742
HEALTHCHECK --interval=10s --timeout=30s --start-period=60s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8742/health', timeout=25)"
USER nobody
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8742"]
