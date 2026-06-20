"""
Tests for URL validation and path traversal defense.
"""

import pytest
from app.worker.cloner import validate_github_url, safe_read_file, ClonerError
from pathlib import Path
import tempfile
import os


class TestValidateGitHubUrl:
    """Test strict GitHub URL validation."""

    def test_valid_url(self):
        owner, repo = validate_github_url("https://github.com/fastapi/fastapi")
        assert owner == "fastapi"
        assert repo == "fastapi"

    def test_valid_url_with_git_suffix(self):
        owner, repo = validate_github_url("https://github.com/fastapi/fastapi.git")
        assert owner == "fastapi"
        assert repo == "fastapi"

    def test_valid_url_trailing_slash(self):
        owner, repo = validate_github_url("https://github.com/owner/repo/")
        assert owner == "owner"
        assert repo == "repo"

    def test_rejects_file_scheme(self):
        with pytest.raises(ClonerError, match="file://"):
            validate_github_url("file:///etc/passwd")

    def test_rejects_http(self):
        with pytest.raises(ClonerError, match="HTTPS"):
            validate_github_url("http://github.com/owner/repo")

    def test_rejects_non_github(self):
        with pytest.raises(ClonerError, match="github.com"):
            validate_github_url("https://gitlab.com/owner/repo")

    def test_rejects_empty(self):
        with pytest.raises(ClonerError):
            validate_github_url("")

    def test_rejects_relative_path(self):
        with pytest.raises(ClonerError):
            validate_github_url("../../../etc/passwd")

    def test_rejects_local_path(self):
        with pytest.raises(ClonerError):
            validate_github_url("/home/user/myrepo")

    def test_rejects_malformed_owner(self):
        # GitHub usernames can't start or end with hyphens
        with pytest.raises(ClonerError):
            validate_github_url("https://github.com/-badowner/repo")

    def test_rejects_missing_repo(self):
        with pytest.raises(ClonerError):
            validate_github_url("https://github.com/onlyowner")

    def test_owner_with_hyphens(self):
        owner, repo = validate_github_url("https://github.com/my-org/my-repo")
        assert owner == "my-org"
        assert repo == "my-repo"

    def test_owner_with_dots(self):
        owner, repo = validate_github_url("https://github.com/my.org/my.repo")
        assert owner == "my.org"
        assert repo == "my.repo"


class TestPathTraversalDefense:
    """Test that safe_read_file prevents path traversal attacks."""

    def test_reads_file_inside_job_dir(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"print('hello')")
        result = safe_read_file(test_file, tmp_path)
        assert result == b"print('hello')"

    def test_rejects_file_outside_job_dir(self, tmp_path):
        # Create a file outside the job dir
        outside_dir = tmp_path.parent
        outside_file = outside_dir / "secret.txt"
        outside_file.write_bytes(b"secret")

        try:
            result = safe_read_file(outside_file, tmp_path)
            assert result is None
        finally:
            outside_file.unlink(missing_ok=True)

    def test_rejects_symlink_escaping_job_dir(self, tmp_path):
        # Create a symlink inside job_dir pointing outside
        with tempfile.NamedTemporaryFile(delete=False, dir=tmp_path.parent) as f:
            f.write(b"evil content")
            outside_file = Path(f.name)

        symlink = tmp_path / "escape.py"
        symlink.symlink_to(outside_file)

        try:
            result = safe_read_file(symlink, tmp_path)
            assert result is None
        finally:
            symlink.unlink(missing_ok=True)
            outside_file.unlink(missing_ok=True)

    def test_reads_nested_file_inside_job_dir(self, tmp_path):
        nested = tmp_path / "src" / "deep" / "module.py"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"x = 1")
        result = safe_read_file(nested, tmp_path)
        assert result == b"x = 1"
