FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal for Phase 2; PDF/OCR-specific system packages
# (e.g. for PyMuPDF/PaddleOCR) will be added in the phases that introduce them.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
