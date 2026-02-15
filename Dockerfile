FROM python:3.11-slim

WORKDIR /app

# Install Tesseract OCR engine (needed by pytesseract for screenshot text extraction)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-kor \
      tesseract-ocr-jpn \
      tesseract-ocr-spa \
      tesseract-ocr-por \
      tesseract-ocr-chi-sim \
      tesseract-ocr-chi-tra \
      tesseract-ocr-rus \
      tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
