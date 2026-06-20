FROM python:3.12-slim

# Install system deps: git (for cloning) + build tools (for some pip packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /backend

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the default local embedding model so the worker doesn't
# need network access at indexing time. If you switch providers via
# EMBEDDING_PROVIDER=openai, this layer is effectively a no-op.
ARG PRELOAD_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${PRELOAD_EMBEDDING_MODEL}')" || true

# Ensure temp clone dir exists
RUN mkdir -p /tmp/repomind
