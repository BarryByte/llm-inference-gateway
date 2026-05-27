FROM python:3.11-slim

WORKDIR /app

# Copy source first so pip install can find the package
COPY pyproject.toml .
COPY gateway/ gateway/

# install CPU-only torch first prevents sentence-transformers from pulling
# ~1.5GB of CUDA packages that serve no purpose on a non-NVIDIA machine.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir .

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
