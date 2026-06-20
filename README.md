# RepoMind

A full-stack Retrieval-Augmented Generation (RAG) system for querying public GitHub repositories in natural language.

RepoMind clones a repository, parses the code (using Tree-sitter), chunks it semantically, embeds it into a vector database (pgvector), and lets you chat with it. The AI's answers are fully grounded in the retrieved code, complete with clickable citations pointing to specific file lines and syntax-highlighted snippets.

## Features
- **Semantic Code Chunking**: Preserves functions and class boundaries for better context.
- **Strict Grounding**: Citations are generated from retrieved metadata, not LLM hallucinations.
- **Premium UI**: Glassmorphism, animations, dark mode, syntax highlighting, and SSE streaming.
- **Robust Orchestration**: Asynchronous indexing via Celery workers so large repositories don't block the API.

---

## 🚀 Quickstart: Local Docker Deployment

The entire stack is configured via a single `docker-compose.yml` file.

### 1. Configure Environment Variables

```bash
# Copy the example env file
cp .env.example .env
```

Open `.env` and configure your API keys:
```env
# Required: Get a free key at https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your_gemini_api_key_here

# Optional: To use OpenAI embeddings/LLM (see 'Switching Providers' below)
# OPENAI_API_KEY=sk-...
```

### 2. Start the Stack

```bash
docker-compose up --build
```

This starts 6 containers:
- `postgres` (pgvector 16)
- `redis`
- `migrate` (one-shot DB schema migrations)
- `worker` (Celery worker for heavy indexing tasks)
- `api` (FastAPI backend on port 8000)
- `frontend` (React + Nginx on port 3000)

### 3. Use the Application

Open your browser to: **http://localhost:3000**

1. Paste a public GitHub URL (e.g., `https://github.com/fastapi/fastapi`).
2. Wait for the background worker to clone, parse, and embed the repository.
3. Start chatting! Click the citation chips to see the syntax-highlighted code.

---

## 🛠 Switching AI Providers

By default, RepoMind uses **Gemini 2.5 Flash** for generation and a **local BAAI/bge-small-en-v1.5** model for embeddings (which downloads automatically inside the backend container).

You can switch to OpenAI by modifying the `.env` file (ensure you add `OPENAI_API_KEY`):

```env
# Switch LLM to OpenAI
LLM_PROVIDER=openai

# Switch Embeddings to OpenAI (text-embedding-3-small)
EMBEDDING_PROVIDER=openai
```

> **⚠️ Important Re-indexing Note:** If you switch embedding providers, the vector dimensions will change (e.g. BGE is 384, OpenAI is 1536). You must re-index your repositories, or you'll get a dimensionality error when chatting.

## Limitations
- **Public Repos Only**: Private repositories and SSH authentication are not supported.
- **Supported Languages**: Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin, HTML, CSS (and plain-text indexing for `.md`, `.json`, `.toml`, `.yaml`, `.sql`, `.sh`, `.env`, `Dockerfile`).
- **No Auth**: This is a single-tenant local application; there are no user accounts.
