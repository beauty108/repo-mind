# RepoMind Backend

A FastAPI-based Retrieval-Augmented Generation (RAG) system for querying public GitHub repositories in natural language. Users submit a GitHub URL, the system indexes it (Tree-sitter parsing + semantic chunking + vector embeddings), and then answers natural-language questions grounded in the actual code with file/line citations.

---

## Running the Backend Standalone

### Prerequisites

- Python 3.12+
- PostgreSQL 15+ with the **pgvector** extension (`pgvector/pgvector` Docker image includes it)
- Redis 7+
- `git` CLI available on `$PATH`

### Option A — Docker Compose (recommended)

```bash
cd backend

# Copy and configure environment
cp .env.example .env
# Edit .env: set GEMINI_API_KEY (required), optionally OPENAI_API_KEY

# Start postgres + redis, run migrations, then start API + worker
docker-compose up --build
```

Services started:
- `postgres` on port `5432`
- `redis` on port `6379`
- `migrate` (one-shot Alembic migration runner)
- `api` on port `8000` (`uvicorn`)
- `worker` (Celery worker with concurrency=2)

### Option B — Local (venv)

```bash
cd backend

# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env: DATABASE_URL, REDIS_URL, GEMINI_API_KEY

# 4. Start Postgres and Redis (e.g. via Docker or Homebrew)
docker run -d -p 5432:5432 -e POSTGRES_USER=repomind \
  -e POSTGRES_PASSWORD=repomind -e POSTGRES_DB=repomind \
  pgvector/pgvector:pg16

docker run -d -p 6379:6379 redis:7-alpine

# 5. Run database migrations
alembic upgrade head

# 6. (Terminal 1) Start the FastAPI API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. (Terminal 2) Start the Celery worker
celery -A app.worker.celery_app worker --loglevel=info --concurrency=2

# 8. (Optional) Start Celery Flower for task monitoring
pip install flower
celery -A app.worker.celery_app flower --port=5555
```

---

## Acceptance Test

Run against the real `fastapi/fastapi` repository:

```bash
# 1. Submit the repository
REPO_RESPONSE=$(curl -s -X POST http://localhost:8000/api/repos \
  -H 'Content-Type: application/json' \
  -d '{"github_url": "https://github.com/fastapi/fastapi"}')

REPO_ID=$(echo $REPO_RESPONSE | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Repo ID: $REPO_ID"

# 2. Poll until ready (indexing takes 3–10 minutes depending on machine)
watch -n 10 "curl -s http://localhost:8000/api/repos/$REPO_ID | python -c \"import sys,json; r=json.load(sys.stdin); print(r['status'], r.get('indexed_file_count', 0), 'files')\""

# 3. Chat (SSE streaming)
curl -N -X POST http://localhost:8000/api/repos/$REPO_ID/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "How does dependency injection work?"}'

# Run all 5 acceptance queries
for Q in \
  "How does dependency injection work?" \
  "Where are route decorators implemented?" \
  "Explain the request lifecycle." \
  "Which files define middleware behavior?" \
  "How are request parameters validated?"
do
  echo "=== $Q ==="
  curl -N -s -X POST http://localhost:8000/api/repos/$REPO_ID/chat \
    -H 'Content-Type: application/json' \
    -d "{\"message\": \"$Q\"}" | grep -o '"content":"[^"]*"' | sed 's/"content"://g'
  echo
done
```

---

## API Contract

All endpoints return JSON. Errors use `{"detail": "human-readable message"}`. Stack traces are **never** exposed in API responses.

### `POST /api/repos`

Submit a GitHub repository for indexing.

**Request:**
```json
{ "github_url": "https://github.com/fastapi/fastapi" }
```

**Response (202 Accepted):**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "github_url": "https://github.com/fastapi/fastapi",
  "owner": "fastapi",
  "repo_name": "fastapi",
  "status": "pending",
  "error_message": null,
  "indexed_file_count": 0,
  "skipped_file_count": 0,
  "indexed_commit_sha": null,
  "embedding_model_name": null,
  "created_at": "2026-06-17T13:00:00Z",
  "updated_at": "2026-06-17T13:00:00Z"
}
```

**Status lifecycle:** `pending` → `indexing` → `ready` | `failed`

**Idempotency:**
- If the repo is already `ready` and the remote HEAD SHA matches the stored SHA, re-submitting marks it `pending` but the Celery task will detect the SHA match and skip full re-indexing.
- If the repo is `failed`, re-submitting retries the indexing.

**Rate limit:** 5 requests/minute per IP.

---

### `GET /api/repos/{id}`

Poll indexing status.

**Response (200):** Same shape as POST response, with updated `status`, `indexed_file_count`, `skipped_file_count`.

```json
{
  "id": "...",
  "status": "ready",
  "indexed_file_count": 142,
  "skipped_file_count": 8,
  "indexed_commit_sha": "abc123...",
  "embedding_model_name": "BAAI/bge-small-en-v1.5"
}
```

**Error states:**
- `404` — Repository not found.
- `status: "failed"` with `error_message` — Human-readable failure reason.

---

### `GET /api/repos/{id}/files?page=1&page_size=50`

Paginated list of indexed files.

**Query params:** `page` (default 1), `page_size` (1–200, default 50).

**Response (200):**
```json
{
  "items": [
    { "id": "...", "path": "fastapi/routing.py", "language": "python", "size_bytes": 42123 }
  ],
  "total": 142,
  "page": 1,
  "page_size": 50
}
```

**Errors:** `404` if repo not found, `409` if repo not in `ready` state.

---

### `POST /api/repos/{id}/chat`

Send a question and receive a streaming SSE response.

**Request:**
```json
{
  "message": "How does dependency injection work?",
  "conversation_id": "optional-uuid"
}
```

**Response:** `text/event-stream` (SSE)

Each event is a `data:` line containing a JSON object:

```
data: {"type": "token", "content": "Dependency"}
data: {"type": "token", "content": " injection"}
data: {"type": "token", "content": " in FastAPI..."}

data: {
  "type": "done",
  "citations": [
    {
      "file_path": "fastapi/dependencies/utils.py",
      "start_line": 45,
      "end_line": 89,
      "symbol_name": "solve_dependencies",
      "symbol_type": "function",
      "language": "python",
      "similarity": 0.8923
    }
  ],
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message_id": "7bc12f64-5717-4562-b3fc-2c963f66afa6"
}

data: {"type": "error", "detail": "Error message"}
```

**Citation guarantee:** Citations are derived from retrieved chunk metadata, **never** from LLM-generated text.

**Errors:** `404` if repo not found, `409` if repo not ready or embedding model mismatch.

**Rate limit:** 10 requests/minute per IP.

**Curl example with streaming:**
```bash
curl -N -X POST http://localhost:8000/api/repos/{id}/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "How does dependency injection work?"}' \
  --no-buffer
```

---

### `GET /api/repos/{id}/conversations/{conv_id}/messages`

Retrieve full chat history for a conversation.

**Response (200):**
```json
{
  "conversation_id": "...",
  "repository_id": "...",
  "messages": [
    {
      "id": "...",
      "conversation_id": "...",
      "role": "user",
      "content": "How does dependency injection work?",
      "citations": null,
      "created_at": "2026-06-17T13:05:00Z"
    },
    {
      "id": "...",
      "conversation_id": "...",
      "role": "assistant",
      "content": "Dependency injection in FastAPI works by...",
      "citations": [
        { "file_path": "fastapi/dependencies/utils.py", "start_line": 45, "end_line": 89, ... }
      ],
      "created_at": "2026-06-17T13:05:05Z"
    }
  ]
}
```

---

### `GET /health`

**Response (200):** `{"status": "ok", "service": "repomind-api", "version": "1.0.0"}`

---

## Switching Embedding and LLM Providers

### Switch to OpenAI Embeddings

```env
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

> **⚠️ Warning:** OpenAI `text-embedding-3-small` produces 1536-dimensional vectors vs BGE's 384. **Any existing indexed repositories must be re-indexed after switching providers.** The API will detect the model mismatch at query time and return a clear error prompting you to re-index.

To re-index after switching:
```bash
curl -X POST http://localhost:8000/api/repos -d '{"github_url": "https://github.com/..."}' -H 'Content-Type: application/json'
```

### Switch to a Different Gemini Model

```env
GEMINI_MODEL=gemini-2.5-flash
```

Verify current free-tier models at: https://ai.google.dev/gemini-api/docs/models

### Future LLM Providers (not yet implemented)

The `LLMClient` interface is designed to support OpenAI, Anthropic, and Ollama. To add a new provider:
1. Create `app/ai/llm_providers/openai.py` implementing `LLMClient`
2. Add a branch in `app/ai/llm_client.py`'s factory function
3. Set `LLM_PROVIDER=openai` in `.env`

---

## Security Constraints

| Constraint | Implementation |
|---|---|
| Only public GitHub repos | Strict regex: `^https://github\.com/[owner]/[repo]$` |
| No code execution | `git clone` and `git rev-parse` only; no `pip install`, no `python <file>` |
| Path traversal defense | All file reads validated via `Path.resolve()` inside job dir |
| Clone timeout | `subprocess.run(timeout=CLONE_TIMEOUT_SECONDS)` |
| Size limit | `du -sb` post-clone, reject if > `MAX_REPO_SIZE_MB` |
| Rate limiting | slowapi on indexing (5/min) and chat (10/min) per IP |
| SQL injection | SQLAlchemy parameterized queries throughout |
| No stack traces in API | Generic error messages in responses; full traces in server logs |

---

## Architecture Overview

```
Client → POST /api/repos
         → FastAPI creates Repository(status=pending)
         → Celery task enqueued: clone_and_index(repo_id)
            → git clone --depth 1 (timeout, size check)
            → Walk files (allowlist: .py .js .jsx .ts .tsx)
            → Tree-sitter parse → functions, classes, imports
            → Semantic chunker → CodeChunkData list
            → EmbeddingClient.embed(texts) → vectors (batched)
            → Upsert: Files, CodeChunks, Embeddings (pgvector)
            → Repository(status=ready, commit_sha=...)
         ← GET /api/repos/{id} polls status

Client → POST /api/repos/{id}/chat
         → Validate repo is ready, model matches
         → EmbeddingClient.embed([question])
         → pgvector cosine similarity → top-k chunks
         → Build prompt: question + chunk context
         → LLMClient.generate(prompt, stream=True)
         ← SSE stream: tokens → citations → done
```

---

## Structured Logs

All logs are JSON with consistent fields. Key log events:

| Event | Fields |
|---|---|
| Clone started | `github_url`, `job_id`, `timeout_seconds` |
| Clone complete | `github_url`, `duration_s` |
| Indexing complete | `indexed_files`, `skipped_files`, `total_chunks`, `commit_sha`, `total_duration_s` |
| Embedding complete | `chunks`, `model`, `duration_s` |
| Vector retrieval | `repo_id`, `top_k`, `retrieved`, `retrieval_latency_s` |
| LLM generation | `latency_s` |
| Indexing failed | `repo_id`, `reason` |
