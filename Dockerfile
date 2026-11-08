FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Credentials are NOT baked into the image: use Cloud Run's service identity,
# or pass GEE_SERVICE_ACCOUNT_KEY (JSON content) as an environment secret.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --timeout-keep-alive 650
