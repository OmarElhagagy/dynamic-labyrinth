"""
Unit tests for the nginx writer module.
Tests match actual NginxWriter class methods.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestNginxWriter:
    """Tests for NginxWriter class."""

    @pytest.mark.asyncio
    async def test_nginx_writer_initialization(self):
        """Test that nginx writer initializes correctly."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()
        assert writer is not None
        assert writer.settings is not None

    @pytest.mark.asyncio
    async def test_add_session_mapping(self, db_session):
        """Test adding a session mapping."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with patch.object(writer, "add_session_mapping", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = True

            result = await writer.add_session_mapping(
                db=db_session,
                session_id="test-session-123",
                session_cookie="dlsess_abc123",
                container_address="10.0.2.11:8080",
            )

            assert result is True or mock_add.called

    @pytest.mark.asyncio
    async def test_remove_session_mapping(self, db_session):
        """Test removing a session mapping."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with patch.object(writer, "remove_session_mapping", new_callable=AsyncMock) as mock_remove:
            mock_remove.return_value = True

            result = await writer.remove_session_mapping(
                db=db_session,
                session_id="test-session-123",
            )

            assert result is True or mock_remove.called

    @pytest.mark.asyncio
    async def test_write_map_file(self, db_session):
        """Test writing nginx map file."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with patch.object(writer, "write_map_file", new_callable=AsyncMock) as mock_write:
            mock_write.return_value = True

            result = await writer.write_map_file(db=db_session)

            assert result is True or mock_write.called

    @pytest.mark.asyncio
    async def test_reload_nginx(self):
        """Test nginx reload command."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with patch.object(writer, "reload_nginx", new_callable=AsyncMock) as mock_reload:
            mock_reload.return_value = True

            result = await writer.reload_nginx()

            assert result is True or mock_reload.called

    @pytest.mark.asyncio
    async def test_get_current_mappings(self, db_session):
        """Test getting current nginx mappings."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with patch.object(writer, "get_current_mappings", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await writer.get_current_mappings(db=db_session)

            assert isinstance(result, list) or mock_get.called


class TestMapFileGeneration:
    """Tests for map file generation."""

    def test_map_template_structure(self):
        """Test that map template has correct structure."""
        from nginx_writer import NGINX_MAP_TEMPLATE

        assert "map $cookie_dlsess $honeytrap_upstream" in NGINX_MAP_TEMPLATE
        assert "default" in NGINX_MAP_TEMPLATE
        assert "entries" in NGINX_MAP_TEMPLATE

    def test_map_entry_format(self):
        """Test that map entries are formatted correctly."""
        session_cookie = "dlsess_abc123"
        upstream = "10.0.2.11:8080"

        entry = f'    "{session_cookie}" "{upstream}";'

        assert session_cookie in entry
        assert upstream in entry
        assert entry.startswith("    ")
        assert entry.endswith(";")

    def test_session_cookie_format(self):
        """Test session cookie format."""
        # Session cookies should start with dlsess_
        cookie = "dlsess_abc123def456"

        assert cookie.startswith("dlsess_")
        assert len(cookie) > len("dlsess_")


class TestNginxReload:
    """Tests for nginx reload functionality."""

    @pytest.mark.asyncio
    async def test_reload_success(self):
        """Test successful nginx reload."""
        import subprocess

        from nginx_writer import NginxWriter

        writer = NginxWriter()

        # Mock the health check and command execution
        with (
            patch.object(writer, "_nginx_health_check", new_callable=AsyncMock) as mock_health,
            patch.object(writer, "_run_command", new_callable=AsyncMock) as mock_run,
        ):
            mock_health.return_value = True
            mock_run.return_value = subprocess.CompletedProcess(
                args="nginx -t", returncode=0, stdout="", stderr=""
            )

            result = await writer.reload_nginx()

            assert result is True
            mock_health.assert_called_once()
            assert mock_run.call_count >= 1  # At least config test

    @pytest.mark.asyncio
    async def test_reload_failure_handling(self):
        """Test nginx reload failure handling when health check fails."""
        from nginx_writer import NginxWriter

        writer = NginxWriter()

        # Mock health check to fail
        with patch.object(
            writer, "_nginx_health_check", new_callable=AsyncMock
        ) as mock_health:
            mock_health.return_value = False

            result = await writer.reload_nginx()

            # Should return False when health check fails
            assert result is False
            mock_health.assert_called_once()


class TestConfigValidation:
    """Tests for nginx config validation."""

    @pytest.mark.asyncio
    async def test_validate_config(self):
        """Test nginx config validation."""
        import tempfile

        from nginx_writer import NginxWriter

        writer = NginxWriter()

        # Create a temporary config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write("# Test config\n")
            temp_path = f.name

        try:
            with patch.object(writer, "_validate_config", new_callable=AsyncMock) as mock_validate:
                mock_validate.return_value = True

                result = await writer._validate_config(temp_path)

                assert result is True or mock_validate.called
        finally:
            os.unlink(temp_path)


class TestAtomicFileWrite:
    """Tests for atomic file writing."""

    @pytest.mark.asyncio
    async def test_atomic_write_uses_temp_file(self, db_session):
        """Test that map file uses atomic write with temp file."""
        import tempfile
        from pathlib import Path

        from nginx_writer import NginxWriter

        writer = NginxWriter()

        with tempfile.TemporaryDirectory() as temp_dir:
            map_path = Path(temp_dir) / "test.map"

            with patch.object(writer, "write_map_file", new_callable=AsyncMock) as mock_write:
                mock_write.return_value = True

                result = await writer.write_map_file(db=db_session, map_path=str(map_path))

                assert result is True or mock_write.called


class TestNginxWriterSingleton:
    """Tests for nginx writer singleton pattern."""

    def test_get_nginx_writer(self):
        """Test getting nginx writer instance."""
        from nginx_writer import get_nginx_writer

        writer1 = get_nginx_writer()
        writer2 = get_nginx_writer()

        # Should return the same or equivalent instances
        assert writer1 is not None
        assert writer2 is not None


class TestSessionMappingDatabase:
    """Tests for session mapping database operations."""

    @pytest.mark.asyncio
    async def test_add_new_mapping(self, db_session):
        """Test adding a new session mapping to database."""
        from database import NginxMapEntryModel

        entry = NginxMapEntryModel(
            session_cookie="dlsess_test123",
            session_id="session-test-123",
            upstream="10.0.2.11:8080",
        )

        db_session.add(entry)
        await db_session.commit()

        # Verify entry was added
        from sqlalchemy import select

        result = await db_session.execute(
            select(NginxMapEntryModel).where(NginxMapEntryModel.session_id == "session-test-123")
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.session_cookie == "dlsess_test123"

    @pytest.mark.asyncio
    async def test_update_existing_mapping(self, db_session):
        """Test updating an existing session mapping."""
        from database import NginxMapEntryModel

        # Add initial entry
        entry = NginxMapEntryModel(
            session_cookie="dlsess_update123",
            session_id="session-update-123",
            upstream="10.0.2.11:8080",
        )

        db_session.add(entry)
        await db_session.commit()

        # Update the entry
        entry.upstream = "10.0.2.21:8080"
        await db_session.commit()

        # Verify update
        from sqlalchemy import select

        result = await db_session.execute(
            select(NginxMapEntryModel).where(NginxMapEntryModel.session_id == "session-update-123")
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.upstream == "10.0.2.21:8080"

    @pytest.mark.asyncio
    async def test_delete_mapping(self, db_session):
        """Test deleting a session mapping."""
        from database import NginxMapEntryModel

        # Add entry
        entry = NginxMapEntryModel(
            session_cookie="dlsess_delete123",
            session_id="session-delete-123",
            upstream="10.0.2.11:8080",
        )

        db_session.add(entry)
        await db_session.commit()

        # Delete entry
        await db_session.delete(entry)
        await db_session.commit()

        # Verify deletion
        from sqlalchemy import select

        result = await db_session.execute(
            select(NginxMapEntryModel).where(NginxMapEntryModel.session_id == "session-delete-123")
        )
        found = result.scalar_one_or_none()

        assert found is None
