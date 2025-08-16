FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

# Production command (recommended)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]

# Development alternative (keep only one CMD)
# CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]