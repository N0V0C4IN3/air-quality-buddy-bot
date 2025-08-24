FROM python:3.11-slim

WORKDIR /app

COPY housekeeper/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY common /app/common
COPY housekeeper /app

ENV PYTHONPATH=/app

CMD ["python", "main.py"]