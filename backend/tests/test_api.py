"""
API integration tests using FastAPI TestClient.

These tests use a mocked database and do not require a real PostgreSQL instance.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked dependencies."""
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            database_url="postgresql+asyncpg://test:test@localhost/test",
            sync_database_url="postgresql+psycopg2://test:test@localhost/test",
            redis_url="redis://localhost:6379/0",
            embedding_provider="local",
            local_embedding_model="BAAI/bge-small-en-v1.5",
            openai_api_key=None,
            llm_provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash",
            max_repo_size_mb=500,
            clone_timeout_seconds=120,
            clone_base_dir="/tmp/repomind",
            top_k=8,
            max_context_chunks=8,
            cors_origins=["http://localhost:5173"],
            rate_limit_index="1000/minute",
            rate_limit_chat="1000/minute",
            chat_cache_ttl=0,
        )
        from app.main import app
        return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestSubmitRepo:
    def test_rejects_non_github_url(self, client):
        response = client.post(
            "/api/repos",
            json={"github_url": "https://gitlab.com/owner/repo"},
        )
        assert response.status_code in (422, 400)

    def test_rejects_local_file_path(self, client):
        response = client.post(
            "/api/repos",
            json={"github_url": "file:///etc/passwd"},
        )
        assert response.status_code in (422, 400)

    def test_rejects_http_url(self, client):
        response = client.post(
            "/api/repos",
            json={"github_url": "http://github.com/owner/repo"},
        )
        assert response.status_code in (422, 400)

    def test_rejects_empty_url(self, client):
        response = client.post(
            "/api/repos",
            json={"github_url": ""},
        )
        assert response.status_code == 422

    def test_rejects_missing_body(self, client):
        response = client.post("/api/repos", json={})
        assert response.status_code == 422


class TestURLValidationSchema:
    """Test that Pydantic schema catches obviously bad URLs before hitting the cloner."""

    def test_github_url_passes_schema(self):
        from app.schemas.repository import RepoSubmitRequest
        req = RepoSubmitRequest(github_url="https://github.com/fastapi/fastapi")
        assert req.github_url == "https://github.com/fastapi/fastapi"

    def test_non_github_url_fails_schema(self):
        from pydantic import ValidationError
        from app.schemas.repository import RepoSubmitRequest
        with pytest.raises(ValidationError):
            RepoSubmitRequest(github_url="https://gitlab.com/owner/repo")

    def test_strips_whitespace(self):
        from app.schemas.repository import RepoSubmitRequest
        req = RepoSubmitRequest(github_url="  https://github.com/owner/repo  ")
        assert req.github_url == "https://github.com/owner/repo"
