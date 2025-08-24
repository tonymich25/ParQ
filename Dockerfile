FROM python:3.9-slim

WORKDIR /app/Car-Project

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .  .

CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", ":5000", "config:app"]