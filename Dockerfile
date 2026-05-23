FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000

CMD gunicorn -w 1 -k gthread --threads 2 -b 0.0.0.0:$PORT app:app --timeout 0
