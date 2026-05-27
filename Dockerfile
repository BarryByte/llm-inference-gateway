FROM python:3.11-slim

WORKDIR /app

# Copy source first so pip install can find the package
COPY pyproject.toml .
COPY gateway/ gateway/

RUN pip install --no-cache-dir .

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
