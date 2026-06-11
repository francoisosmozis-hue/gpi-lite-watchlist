FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/morning data/h5 data/results data/output

CMD ["sh", "-c", "uvicorn api_main:app --host 0.0.0.0 --port ${PORT:-8080}"]