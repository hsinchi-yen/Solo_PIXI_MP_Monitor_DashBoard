# Build context: solo-pixi-essential/  (docker-compose: build.context ., dockerfile api/Dockerfile)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/api

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

# Shared with the desktop tools: parser + splitter live one directory above api/.
COPY module_log_parser.py log_splitter.py /app/
COPY api/ /app/api/

RUN useradd --system --uid 1000 --home /app pixi && mkdir -p /tmp/pixi_uploads && chown pixi /tmp/pixi_uploads
USER pixi

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-keep-alive", "30"]
