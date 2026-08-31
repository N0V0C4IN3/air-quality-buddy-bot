FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev build-essential tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd -m -u 1000 -s /usr/sbin/nologin runner

COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

COPY reporter-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common /app/common
COPY reporter-bot /app

ENV PYTHONPATH=/app
USER runner

CMD ["bash", "-c", "alembic upgrade head && python bot.py"]