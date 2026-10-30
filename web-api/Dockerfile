FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev build-essential tzdata curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd -m -u 1000 -s /usr/sbin/nologin runner

COPY web-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common /app/common
COPY web-api /app

ENV PYTHONPATH=/app
USER runner

EXPOSE 8080

CMD ["python", "main.py"]
