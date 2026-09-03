FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
  && python -c "from natasha import Doc, MorphVocab, NamesExtractor, NewsEmbedding, NewsMorphTagger, NewsNERTagger, Segmenter; emb=NewsEmbedding(); vocab=MorphVocab(); doc=Doc('Игрок усилил существо.'); doc.segment(Segmenter()); doc.tag_morph(NewsMorphTagger(emb)); [token.lemmatize(vocab) for token in doc.tokens]; doc.tag_ner(NewsNERTagger(emb)); NamesExtractor(vocab); assert doc.tokens[0].lemma"
COPY server.py ./
EXPOSE 8742
HEALTHCHECK --interval=10s --timeout=30s --start-period=60s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8742/health', timeout=25)"
USER nobody
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8742"]
