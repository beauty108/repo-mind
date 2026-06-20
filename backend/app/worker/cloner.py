"""
Repository cloner — validates GitHub URLs, shallow-clones repos,
enforces size and timeout limits, and provides path-traversal defense.

Security constraints enforced here:
  - Only https://github.com URLs accepted (strict regex)
  - No local file paths, no other schemes
  - Clone timeout enforced
  - Post-clone size check (rejects repos over MAX_REPO_SIZE_MB)
  - All file paths resolved and confirmed inside job temp dir
  - Temp directories are always cleaned up (success or failure)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# Strict GitHub URL pattern — https://github.com/{owner}/{repo}
# Allows alphanumeric, hyphens, underscores, dots in owner/repo names.
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[a-zA-Z0-9](?:[a-zA-Z0-9_\-\.]*[a-zA-Z0-9])?)"
    r"/(?P<repo>[a-zA-Z0-9_\-\.]+?)(?:\.git)?/?$"
)


class ClonerError(RuntimeError):
    """Raised for all cloner-level errors with human-readable messages."""
    pass


def validate_github_url(url: str) -> tuple[str, str]:
    """
    Validate that `url` is a well-formed public GitHub URL.

    Returns:
        (owner, repo) tuple on success.

    Raises:
        ClonerError: With a human-readable message on invalid input.
    """
    if not url or not isinstance(url, str):
        raise ClonerError("Repository URL must be a non-empty string.")

    url = url.strip()

    # Reject anything that starts with file://, relative paths, or non-https
    if url.startswith("file://"):
        raise ClonerError("Local file paths (file://) are not allowed. Only public GitHub URLs are accepted.")
    if not url.startswith("https://"):
        raise ClonerError(
            "Only HTTPS GitHub URLs are accepted (https://github.com/owner/repo). "
            f"Got: {url!r}"
        )
    if not url.startswith("https://github.com/"):
        raise ClonerError(
            "Only github.com repositories are supported. "
            f"Got: {url!r}"
        )

    m = _GITHUB_URL_RE.match(url)
    if not m:
        raise ClonerError(
            f"Invalid GitHub URL format: {url!r}. "
            "Expected: https://github.com/owner/repo"
        )

    return m.group("owner"), m.group("repo")


def _get_job_dir(job_id: str) -> Path:
    """Return the isolated temp directory for this clone job."""
    settings = get_settings()
    base = Path(settings.clone_base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / job_id


def clone_repository(github_url: str, job_id: str | None = None, github_token: str | None = None) -> Path:
    """
    Shallow-clone a GitHub repository into an isolated temp directory.

    Args:
        github_url: Validated GitHub URL.
        job_id: Optional job identifier; auto-generated if not provided.
        github_token: Optional GitHub Personal Access Token for private repos.
                      When provided, injected into the clone URL — never logged.

    Returns:
        Path to the cloned repository directory.

    Raises:
        ClonerError: On invalid URL, clone failure, timeout, or size exceeded.
    """
    settings = get_settings()
    job_id = job_id or str(uuid.uuid4())

    owner, repo = validate_github_url(github_url)
    clone_url = f"https://github.com/{owner}/{repo}.git"

    job_dir = _get_job_dir(job_id)

    # Inject token for private repos (never log the token)
    if github_token:
        clone_url = f"https://{github_token}@github.com/{owner}/{repo}.git"
        logger.info(
            "Cloning private repository (authenticated)",
            extra={
                "github_url": github_url,  # Public URL without token
                "job_id": job_id,
                "clone_dir": str(job_dir),
                "timeout_seconds": settings.clone_timeout_seconds,
            },
        )
    else:
        logger.info(
            "Cloning repository",
            extra={
                "github_url": github_url,
                "job_id": job_id,
                "clone_dir": str(job_dir),
                "timeout_seconds": settings.clone_timeout_seconds,
            },
        )

    # Remove any stale directory from a previous failed attempt
    if job_dir.exists():
        logger.warning(
            "Clone target already exists — removing stale directory before retry",
            extra={"job_dir": str(job_dir)},
        )
        _cleanup_dir(job_dir)

    try:

        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth", "1",
                "--single-branch",
                "--no-tags",
                clone_url,
                str(job_dir),
            ],
            capture_output=True,
            text=True,
            timeout=settings.clone_timeout_seconds,
            env={**__import__('os').environ, "GIT_TERMINAL_PROMPT": "0"},  # Disable interactive prompts
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Detect private repo / auth failure
            if "Authentication failed" in stderr or "not found" in stderr.lower() or "Repository not found" in stderr:
                if github_token:
                    raise ClonerError(
                        f"Could not clone {github_url!r} with the provided token. "
                        "Please check that the token has 'repo' scope and access to this repository."
                    )
                raise ClonerError(
                    f"Could not clone {github_url!r}. The repository may be private, "
                    "deleted, or the URL may be incorrect. Only public repositories are supported "
                    "(provide a Personal Access Token for private repos)."
                )
            raise ClonerError(
                f"git clone failed for {github_url!r}: {stderr}"
            )

    except subprocess.TimeoutExpired:
        _cleanup_dir(job_dir)
        raise ClonerError(
            f"Cloning {github_url!r} timed out after {settings.clone_timeout_seconds}s. "
            "The repository may be too large or the network is slow."
        )
    except ClonerError:
        _cleanup_dir(job_dir)
        raise
    except Exception as e:
        _cleanup_dir(job_dir)
        raise ClonerError(f"Unexpected error during clone: {e}") from e

    # Post-clone size check
    _check_repo_size(job_dir, github_url, settings.max_repo_size_mb)

    logger.info(
        "Repository cloned successfully",
        extra={"github_url": github_url, "job_id": job_id, "clone_dir": str(job_dir)},
    )
    return job_dir


def _check_repo_size(clone_dir: Path, github_url: str, max_mb: int) -> None:
    """Raise ClonerError if the cloned repo exceeds max_mb megabytes."""
    try:
        result = subprocess.run(
            ["du", "-sb", str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            size_bytes = int(result.stdout.split()[0])
            size_mb = size_bytes / (1024 * 1024)
            if size_mb > max_mb:
                _cleanup_dir(clone_dir)
                raise ClonerError(
                    f"Repository {github_url!r} is too large to index "
                    f"({size_mb:.0f}MB > cap: {max_mb}MB). "
                    "Consider a smaller repository or increase MAX_REPO_SIZE_MB."
                )
    except ClonerError:
        raise
    except Exception as e:
        logger.warning(
            "Could not determine repo size, skipping size check",
            extra={"error": str(e)},
        )


def get_head_commit_sha(clone_dir: Path) -> str | None:
    """Return the HEAD commit SHA of the cloned repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(clone_dir),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.warning("Could not get HEAD SHA", extra={"error": str(e)})
    return None


def get_remote_head_sha(github_url: str) -> str | None:
    """
    Get the remote HEAD commit SHA without cloning.
    Used for re-index deduplication check.
    """
    try:
        owner, repo = validate_github_url(github_url)
        clone_url = f"https://github.com/{owner}/{repo}.git"
        result = subprocess.run(
            ["git", "ls-remote", clone_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            sha = result.stdout.split()[0]
            return sha
    except Exception as e:
        logger.warning(
            "Could not fetch remote HEAD SHA",
            extra={"github_url": github_url, "error": str(e)},
        )
    return None


def safe_read_file(file_path: Path, job_dir: Path) -> bytes | None:
    """
    Read a file only if its resolved path is inside job_dir.
    Defends against path traversal via symlinks or crafted paths in malicious repos.

    Returns:
        File bytes, or None if the path is outside job_dir or unreadable.
    """
    try:
        resolved = file_path.resolve()
        job_resolved = job_dir.resolve()
        if not str(resolved).startswith(str(job_resolved) + os.sep) and resolved != job_resolved:
            logger.warning(
                "Path traversal detected — skipping file",
                extra={"file_path": str(file_path), "resolved": str(resolved)},
            )
            return None
        return resolved.read_bytes()
    except OSError as e:
        logger.warning("Could not read file", extra={"path": str(file_path), "error": str(e)})
        return None


def get_changed_files(clone_dir: Path, old_sha: str, new_sha: str) -> list[str] | None:
    """
    Get the list of files changed between two commits using git diff.
    Used for incremental re-indexing.

    Args:
        clone_dir: Path to the cloned repository.
        old_sha: The previously indexed commit SHA.
        new_sha: The new HEAD commit SHA.

    Returns:
        List of relative file paths that changed, or None if the diff failed.
    """
    try:
        # --name-only gives us just filenames, one per line
        # --diff-filter=ACMR: Added, Copied, Modified, Renamed (skip Deleted)
        result = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                old_sha,
                new_sha,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(clone_dir),
        )

        if result.returncode != 0:
            logger.warning(
                "git diff failed",
                extra={
                    "old_sha": old_sha[:8],
                    "new_sha": new_sha[:8],
                    "stderr": result.stderr.strip()[:200],
                },
            )
            return None

        changed = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        logger.info(
            "Git diff complete",
            extra={
                "old_sha": old_sha[:8],
                "new_sha": new_sha[:8],
                "changed_files": len(changed),
            },
        )
        return changed

    except subprocess.TimeoutExpired:
        logger.warning("git diff timed out")
        return None
    except Exception as e:
        logger.warning("git diff failed unexpectedly", extra={"error": str(e)})
        return None


def cleanup_clone(job_dir: Path) -> None:
    """Delete the job's temp directory (call on success and failure)."""
    _cleanup_dir(job_dir)
    logger.info("Cleaned up clone directory", extra={"dir": str(job_dir)})


def _cleanup_dir(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        logger.warning("Failed to clean up directory", extra={"path": str(path), "error": str(e)})
