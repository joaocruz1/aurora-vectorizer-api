FROM python:3.11-slim

# potrace (binário usado pelo motor) + libs do OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    potrace libglib2.0-0 libgl1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aurora_vectorizer.py service.py ./

EXPOSE 8000
# 2 workers dá conta de uso moderado; suba se precisar de mais paralelismo
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
