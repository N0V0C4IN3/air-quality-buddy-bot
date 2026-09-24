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

# uPlot is vendored at build time rather than pulled from a CDN at page load:
# the dashboard has to work on a LAN with no route out, and a chart library
# fetched over someone else's network is a dependency on their uptime.
ARG UPLOT_VERSION=1.6.31
RUN mkdir -p /app/static/vendor && curl -fsSL -o /app/static/vendor/uPlot.iife.min.js \
      "https://cdn.jsdelivr.net/npm/uplot@${UPLOT_VERSION}/dist/uPlot.iife.min.js" && \
    curl -fsSL -o /app/static/vendor/uPlot.min.css \
      "https://cdn.jsdelivr.net/npm/uplot@${UPLOT_VERSION}/dist/uPlot.min.css"

ENV PYTHONPATH=/app
USER runner

EXPOSE 8080

CMD ["python", "main.py"]
