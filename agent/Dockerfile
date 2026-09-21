FROM python:3.11-slim

WORKDIR /app

# libsndfile1 is required by the Silero VAD audio bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

EXPOSE 8765
CMD ["python", "bot.py"]
