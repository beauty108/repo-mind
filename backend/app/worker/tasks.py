"""
Celery tasks for repository indexing.

Main task: clone_and_index(repo_id)
  1. Fetch Repository record
  2. Check remote HEAD SHA → skip if already indexed at same SHA
  3. Clone → walk files → parse → chunk
  4. Batch-embed all chunks
  5. Upsert CodeChunks + Embeddings to DB
  6. Update Repository status → ready
  7. Cleanup temp dir (always)

Error handling:
  - Any error sets status=failed with a human-readable error_message
  - Never exposes raw stack traces in the DB error_message field
  - Always cleans up the temp clone directory
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import select, delete

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Directories to skip entirely during file walk (in addition to .git)
SKIP_DIRS = frozenset({
    ".git",
    "node_modules",
    "dist",
    "build",
    "out",
    "venv",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".eggs",
    "site-packages",
    "vendor",
    ".next",
    ".nuxt",
    "coverage",
    ".coverage",
    "htmlcoverage",
})

# Only files with these extensions are processed
ALLOWED_EXTENSIONS = frozenset({
    # JavaScript / TypeScript / Python
    ".py", ".js", ".jsx", ".ts", ".tsx",
    # Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx",
    ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
    # Web
    ".html", ".htm", ".css", ".scss", ".less",
    # Plain text / config
    ".sql", ".yaml", ".yml", ".md", ".json", ".toml", ".sh", ".env",
    # Docker
    ".dockerfile",
})

# Extensions that use Tree-sitter AST parsing
TREE_SITTER_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx",
    ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
    ".html", ".htm", ".css", ".scss", ".less",
})

# Extensions processed as single plain-text chunks
PLAINTEXT_EXTENSIONS = frozenset({
    ".sql", ".yaml", ".yml", ".md", ".json", ".toml", ".sh", ".env", ".dockerfile"
})

def _make_plaintext_chunk(source_bytes: bytes, rel_path: str, ext: str):
    from app.worker.chunker import CodeChunkData
    try:
        text = source_bytes.decode("utf-8", errors="replace")
    except Exception:
        return None, None
        
    lang_map = {
        ".sql": "sql",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".sh": "bash",
        ".env": "plaintext",
    }
    lang = lang_map.get(ext, "plaintext")
    if rel_path.lower().endswith("dockerfile"):
        lang = "dockerfile"
        
    chunk_data = CodeChunkData(
        content=f"# File: {rel_path}\n\n{text}",
        file_path=rel_path,
        language=lang,
        start_line=1,
        end_line=len(text.splitlines()) or 1,
        symbol_name=None,
        symbol_type="module",
    )
    return chunk_data, lang


def _process_file_worker(abs_path: Path, clone_dir: Path) -> tuple[str, str, int, list] | None:
    """
    Picklable worker function for multiprocessing parsing.
    Returns: (rel_path, language, size_bytes, chunks_list) or None if skipped.
    """
    from app.worker.cloner import safe_read_file
    source_bytes = safe_read_file(abs_path, clone_dir)
    if source_bytes is None:
        return None

    ext = abs_path.suffix.lower()
    rel_path = str(abs_path.relative_to(clone_dir))
    
    if ext in PLAINTEXT_EXTENSIONS or abs_path.name.lower() == "dockerfile":
        chunk_data, lang = _make_plaintext_chunk(source_bytes, rel_path, ext)
        if chunk_data is None:
            return None
        return rel_path, lang, len(source_bytes), [chunk_data]
    else:
        from app.worker.parser import parse_file
        from app.worker.chunker import chunk_parsed_file
        parsed = parse_file(abs_path, clone_dir, source_bytes)
        if parsed is None:
            return None
        file_chunks = chunk_parsed_file(parsed)
        return rel_path, parsed.language, parsed.size_bytes, file_chunks


@celery_app.task(
    name="repomind.clone_and_index",
    bind=True,
    max_retries=0,       # Don't auto-retry indexing — caller can re-submit
    acks_late=True,
)
def clone_and_index(self, repo_id: str, github_token: str | None = None) -> dict:
    """
    Celery task: clone and index a GitHub repository.

    Args:
        repo_id: UUID string of the Repository record.
        github_token: Optional GitHub Personal Access Token for private repos.

    Returns:
        Dict with status and counts.
    """
    task_start = time.time()
    repo_uuid = uuid.UUID(repo_id)

    logger.info("Indexing task started", extra={"repo_id": repo_id, "task_id": self.request.id})

    # All DB operations are sync here (Celery worker uses sync SQLAlchemy)
    # We use a sync session factory derived from the async engine's URL.
    from app.config import get_settings
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    settings = get_settings()
    sync_engine = create_engine(
        settings.sync_database_url,
        pool_pre_ping=True,
        pool_size=5,
    )

    clone_dir: Path | None = None

    with Session(sync_engine) as db:
        try:
            # 1. Fetch repository
            from app.models.repository import Repository, RepositoryStatus
            repo = db.get(Repository, repo_uuid)
            if repo is None:
                logger.error("Repository not found", extra={"repo_id": repo_id})
                return {"status": "error", "message": "Repository not found"}

            github_url = repo.github_url

            # 2. Check remote HEAD SHA for re-index dedup
            from app.worker.cloner import get_remote_head_sha, validate_github_url
            validate_github_url(github_url)

            logger.info("Fetching remote HEAD SHA", extra={"github_url": github_url})
            remote_sha = get_remote_head_sha(github_url)

            if (
                remote_sha
                and repo.indexed_commit_sha == remote_sha
                and repo.status == RepositoryStatus.ready
            ):
                logger.info(
                    "Repository already indexed at current SHA — skipping re-index",
                    extra={"github_url": github_url, "sha": remote_sha},
                )
                return {
                    "status": "skipped",
                    "reason": "Already indexed at current commit SHA",
                    "sha": remote_sha,
                }

            # 3. Mark as indexing
            repo.status = RepositoryStatus.indexing
            repo.error_message = None
            db.commit()

            # 4. Clone
            from app.worker.cloner import clone_repository, cleanup_clone, get_head_commit_sha, safe_read_file
            clone_start = time.time()
            clone_dir = clone_repository(
                github_url,
                job_id=str(self.request.id or uuid.uuid4()),
                github_token=github_token,
            )
            clone_duration = time.time() - clone_start
            logger.info(
                "Clone completed",
                extra={"github_url": github_url, "duration_s": round(clone_duration, 2)},
            )

            # Get commit SHA from local clone (more reliable than remote ls-remote)
            commit_sha = get_head_commit_sha(clone_dir) or remote_sha

            # 5. Walk files — do incremental diff if re-indexing
            walk_start = time.time()
            if (
                repo.indexed_commit_sha
                and remote_sha
                and repo.indexed_commit_sha != remote_sha
            ):
                # Incremental: only re-process changed files
                from app.worker.cloner import get_changed_files
                changed_paths = get_changed_files(clone_dir, repo.indexed_commit_sha, remote_sha)
                if changed_paths is not None:
                    logger.info(
                        "Incremental re-index: processing only changed files",
                        extra={"changed_files": len(changed_paths)},
                    )
                    files_to_index, skipped_files = _walk_specific_files(clone_dir, changed_paths)
                    is_incremental = True
                else:
                    # Diff failed — fall back to full re-index
                    logger.warning("Git diff failed, falling back to full re-index")
                    files_to_index, skipped_files = _walk_files(clone_dir)
                    is_incremental = False
            else:
                files_to_index, skipped_files = _walk_files(clone_dir)
                is_incremental = False

            walk_duration = time.time() - walk_start

            if not files_to_index:
                if is_incremental:
                    # Incremental re-index with no changed source files — mark ready
                    logger.info("Incremental re-index: no source files changed, skipping embedding")
                    repo.status = RepositoryStatus.ready
                    repo.indexed_commit_sha = commit_sha
                    db.commit()
                    return {"status": "ready", "reason": "No source files changed"}
                else:
                    _set_failed(db, repo, "No supported source files found in this repository (supported: .py, .js, .jsx, .ts, .tsx, .go, .rs, .java, .c, .h, .cpp, .cc, .cxx, .hpp, .hxx, .cs, .rb, .php, .swift, .kt, .kts, .html, .htm, .css, .scss, .less, .sql, .yaml, .yml, .md, .json, .toml, .sh, .env, Dockerfile).")
                    return {"status": "failed"}

            logger.info(
                "File walk complete",
                extra={
                    "total_files": len(files_to_index),
                    "skipped_files": skipped_files,
                    "duration_s": round(walk_duration, 2),
                },
            )

            # 6. Parse and chunk
            parse_start = time.time()
            import concurrent.futures

            all_chunks: list = []
            indexed_file_paths: list[tuple[str, str, int]] = []  # (rel_path, language, size)

            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Submit all parsing jobs
                futures = {executor.submit(_process_file_worker, p, clone_dir): p for p in files_to_index}
                for future in concurrent.futures.as_completed(futures):
                    res = future.result()
                    if res is None:
                        skipped_files += 1
                        continue
                    
                    rel_path, lang, size, file_chunks = res
                    indexed_file_paths.append((rel_path, lang, size))
                    
                    if not file_chunks:
                        logger.debug("No chunks for file", extra={"path": rel_path})
                        continue

                    all_chunks.extend(file_chunks)

            parse_duration = time.time() - parse_start
            logger.info(
                "Parsing and chunking complete",
                extra={
                    "total_chunks": len(all_chunks),
                    "indexed_files": len(indexed_file_paths),
                    "duration_s": round(parse_duration, 2),
                },
            )

            if not all_chunks:
                _set_failed(db, repo, "Parsed files but produced no indexable chunks. The repository may contain only configuration files or unsupported content.")
                return {"status": "failed"}

            # 7. Batch embed all chunks
            embed_start = time.time()
            from app.ai.embedding_client import get_embedding_client, EmbeddingError
            embedding_client = get_embedding_client()
            model_name = embedding_client.model_name

            # Validate model consistency (warn if re-indexing with different model)
            if repo.embedding_model_name and repo.embedding_model_name != model_name:
                logger.warning(
                    "Re-indexing with a different embedding model — old embeddings will be replaced",
                    extra={
                        "old_model": repo.embedding_model_name,
                        "new_model": model_name,
                    },
                )

            texts = [chunk.content for chunk in all_chunks]
            try:
                vectors = embedding_client.embed(texts)
            except EmbeddingError as e:
                _set_failed(db, repo, f"Embedding failed: {e}")
                return {"status": "failed"}

            embed_duration = time.time() - embed_start
            logger.info(
                "Embedding complete",
                extra={
                    "chunks": len(texts),
                    "model": model_name,
                    "duration_s": round(embed_duration, 2),
                },
            )

            # 8. Upsert to DB — for incremental, only delete changed files' data; for full, delete all
            db_start = time.time()
            if is_incremental and indexed_file_paths:
                _upsert_incremental(db, repo, indexed_file_paths, all_chunks, vectors, model_name)
            else:
                _upsert_index(db, repo, indexed_file_paths, all_chunks, vectors, model_name)
            db_duration = time.time() - db_start

            # 9. Mark ready
            from app.models.repository import RepositoryStatus
            repo.status = RepositoryStatus.ready
            repo.indexed_commit_sha = commit_sha
            repo.embedding_model_name = model_name
            repo.indexed_file_count = len(indexed_file_paths)
            repo.skipped_file_count = skipped_files
            repo.error_message = None
            db.commit()

            total_duration = time.time() - task_start
            logger.info(
                "Indexing complete",
                extra={
                    "repo_id": repo_id,
                    "github_url": github_url,
                    "indexed_files": len(indexed_file_paths),
                    "skipped_files": skipped_files,
                    "total_chunks": len(all_chunks),
                    "commit_sha": commit_sha,
                    "incremental": is_incremental,
                    "total_duration_s": round(total_duration, 2),
                    "clone_duration_s": round(clone_duration, 2),
                    "embed_duration_s": round(embed_duration, 2),
                    "db_duration_s": round(db_duration, 2),
                },
            )

            # Invalidate BM25 corpus cache so next query picks up fresh data
            try:
                from app.services.bm25_search import invalidate_bm25_cache
                invalidate_bm25_cache(repo_id)
            except Exception as bm25_err:
                logger.debug("BM25 cache invalidation failed", extra={"error": str(bm25_err)})

            return {
                "status": "ready",
                "indexed_files": len(indexed_file_paths),
                "skipped_files": skipped_files,
                "total_chunks": len(all_chunks),
                "commit_sha": commit_sha,
            }

        except Exception as e:
            logger.exception(
                "Indexing task failed with unexpected error",
                extra={"repo_id": repo_id, "error": str(e)},
            )
            try:
                from app.models.repository import Repository, RepositoryStatus
                repo = db.get(Repository, repo_uuid)
                if repo:
                    _set_failed(db, repo, _safe_error_message(e))
            except Exception:
                pass
            return {"status": "failed", "error": str(e)}

        finally:
            if clone_dir is not None:
                from app.worker.cloner import cleanup_clone
                cleanup_clone(clone_dir)


def _walk_files(clone_dir: Path) -> tuple[list[Path], int]:
    """
    Walk the cloned repo directory and return (files_to_index, skipped_count).

    Skipped count covers: unsupported extensions, binary files, skipped dirs.
    """
    files_to_index: list[Path] = []
    skipped = 0

    for root, dirs, files in os.walk(clone_dir):
        # Prune directories in-place (modifies os.walk's traversal)
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for filename in files:
            abs_path = Path(root) / filename
            ext = abs_path.suffix.lower()
            if ext in ALLOWED_EXTENSIONS:
                files_to_index.append(abs_path)
            else:
                skipped += 1

    return files_to_index, skipped


def _upsert_index(
    db,
    repo,
    indexed_file_paths: list[tuple[str, str, int]],
    all_chunks: list,
    vectors: list[list[float]],
    model_name: str,
) -> None:
    """
    Delete existing index data for this repo and insert fresh records.
    Runs inside the caller's session/transaction.
    """
    from app.models.file import File
    from app.models.chunk import CodeChunk, SymbolType
    from app.models.embedding import Embedding

    # Delete old files (cascades to chunks → embeddings)
    db.execute(delete(File).where(File.repository_id == repo.id))
    db.flush()

    # Build file path → File record map
    file_map: dict[str, File] = {}
    for rel_path, language, size_bytes in indexed_file_paths:
        file_rec = File(
            repository_id=repo.id,
            path=rel_path,
            language=language,
            size_bytes=size_bytes,
        )
        db.add(file_rec)
        file_map[rel_path] = file_rec

    db.flush()  # Assign file IDs

    # Insert chunks and embeddings
    chunk_recs = []
    chunk_vectors = []
    for chunk_data, vector in zip(all_chunks, vectors):
        file_rec = file_map.get(chunk_data.file_path)
        if file_rec is None:
            continue

        # Map symbol_type string to enum
        if chunk_data.symbol_type == "function":
            sym_type = SymbolType.function
        elif chunk_data.symbol_type == "class":
            sym_type = SymbolType.cls
        else:
            sym_type = SymbolType.module

        chunk_rec = CodeChunk(
            file_id=file_rec.id,
            content=chunk_data.content,
            symbol_name=chunk_data.symbol_name,
            symbol_type=sym_type,
            start_line=chunk_data.start_line,
            end_line=chunk_data.end_line,
        )
        chunk_recs.append(chunk_rec)
        chunk_vectors.append(vector)
        
    if chunk_recs:
        db.add_all(chunk_recs)
        db.flush()  # Bulk insert chunks, automatically fetching IDs

        embedding_recs = [
            Embedding(
                chunk_id=chunk_rec.id,
                vector=vector,
                model_name=model_name,
            )
            for chunk_rec, vector in zip(chunk_recs, chunk_vectors)
        ]
        db.add_all(embedding_recs)

    db.flush()


def _walk_specific_files(
    clone_dir: Path,
    changed_paths: list[str],
) -> tuple[list[Path], int]:
    """
    Return only the files from changed_paths that exist in clone_dir
    and have supported extensions. Used for incremental re-indexing.
    """
    files_to_index: list[Path] = []
    skipped = 0

    for rel_path in changed_paths:
        abs_path = clone_dir / rel_path
        if not abs_path.exists():
            # File was deleted in this commit — skip (deletion is handled by _upsert_incremental)
            skipped += 1
            continue
        ext = abs_path.suffix.lower()
        if abs_path.name.lower() == "dockerfile":
            files_to_index.append(abs_path)
        elif ext in ALLOWED_EXTENSIONS or ext in PLAINTEXT_EXTENSIONS:
            files_to_index.append(abs_path)
        else:
            skipped += 1

    return files_to_index, skipped


def _upsert_incremental(
    db,
    repo,
    indexed_file_paths: list[tuple[str, str, int]],
    all_chunks: list,
    vectors: list[list[float]],
    model_name: str,
) -> None:
    """
    Incremental upsert: delete only the changed files' existing data, then insert fresh.
    Preserves unchanged files' chunks and embeddings in the DB.
    """
    from app.models.file import File
    from app.models.chunk import CodeChunk, SymbolType
    from app.models.embedding import Embedding

    # Only delete files that are being re-indexed
    paths_to_delete = [rel_path for rel_path, _, _ in indexed_file_paths]
    if paths_to_delete:
        db.execute(
            delete(File).where(
                File.repository_id == repo.id,
                File.path.in_(paths_to_delete),
            )
        )
        db.flush()

    # Insert fresh data for changed files
    file_map: dict[str, File] = {}
    for rel_path, language, size_bytes in indexed_file_paths:
        file_rec = File(
            repository_id=repo.id,
            path=rel_path,
            language=language,
            size_bytes=size_bytes,
        )
        db.add(file_rec)
        file_map[rel_path] = file_rec

    db.flush()  # Assign file IDs

    chunk_recs = []
    chunk_vectors = []
    for chunk_data, vector in zip(all_chunks, vectors):
        file_rec = file_map.get(chunk_data.file_path)
        if file_rec is None:
            continue

        if chunk_data.symbol_type == "function":
            sym_type = SymbolType.function
        elif chunk_data.symbol_type == "class":
            sym_type = SymbolType.cls
        else:
            sym_type = SymbolType.module

        chunk_rec = CodeChunk(
            file_id=file_rec.id,
            content=chunk_data.content,
            symbol_name=chunk_data.symbol_name,
            symbol_type=sym_type,
            start_line=chunk_data.start_line,
            end_line=chunk_data.end_line,
        )
        chunk_recs.append(chunk_rec)
        chunk_vectors.append(vector)
        
    if chunk_recs:
        db.add_all(chunk_recs)
        db.flush()

        embedding_recs = [
            Embedding(
                chunk_id=chunk_rec.id,
                vector=vector,
                model_name=model_name,
            )
            for chunk_rec, vector in zip(chunk_recs, chunk_vectors)
        ]
        db.add_all(embedding_recs)

    db.flush()


def _set_failed(db: Session, repo: Repository, error_msg: str) -> None:
    """Set repository status to failed with a human-readable error message."""
    from app.models.repository import RepositoryStatus
    repo.status = RepositoryStatus.failed
    repo.error_message = error_msg
    db.commit()
    logger.error("Repository indexing failed", extra={"repo_id": str(repo.id), "reason": error_msg})


def _safe_error_message(exc: Exception) -> str:
    """
    Convert an exception to a human-readable message.
    Never exposes stack traces or internal paths.
    """
    from app.worker.cloner import ClonerError
    from app.ai.embedding_client import EmbeddingError

    if isinstance(exc, ClonerError):
        return str(exc)
    if isinstance(exc, EmbeddingError):
        return f"Embedding error: {exc}"
    # Generic message for unexpected errors — details are in server logs
    return (
        "An unexpected error occurred during indexing. "
        "Please try again or contact support if the issue persists."
    )
