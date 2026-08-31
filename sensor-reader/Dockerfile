# sensor-reader/Dockerfile
FROM python:3.11-slim

# For psycopg2 & serial
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

COPY common /app/common
COPY sensor-reader/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY sensor-reader/ .

# Use non-root
ARG TTY_GID=20
RUN useradd -m runner
RUN set -eux; \
    if getent group "${TTY_GID}" > /dev/null; then \
        grp="$(getent group "${TTY_GID}" | cut -d: -f1)"; \
        usermod -aG "${grp}" runner; \
    else \
        groupadd -g "${TTY_GID}" hostdialout; \
        usermod -aG hostdialout runner; \
    fi
USER runner

# If you need to access /dev/ttyUSB0 inside container, you’ll mount it in compose.
ENV PYTHONUNBUFFERED=1

CMD ["bash", "-c", "alembic upgrade head && python main.py"]
