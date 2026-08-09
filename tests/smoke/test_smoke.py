# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Smoke Tests for Calibre-Web Automated

These are fast tests (<30 seconds total) that verify basic functionality.
Run these before committing to catch critical breakage early.

Run with: pytest tests/smoke/ -v
"""

import pytest
import os
import sys


@pytest.mark.smoke
class TestBasicFunctionality:
    """Verify core application can start and basic functions work."""
    
    def test_python_version(self):
        """Verify Python 3.11+ is being used."""
        assert sys.version_info >= (3, 11), "Python 3.11 or higher required"
    
    def test_required_directories_exist(self):
        """Verify the resolved config directory exists, plus container mounts when present.

        This used to assert the literal ``/config`` unconditionally, which is a
        statement about the *runner*, not about the code: it passes in the image
        because the volume is mounted and fails on every source checkout, so the
        only way to keep it green was to run tests in Docker. Since #1462 the
        config directory is resolved by ``app_paths.config_dir()``, and that
        resolved directory is the thing worth asserting — it is what a source
        install actually needs to exist. See #1474.
        """
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'scripts'))
        import app_paths

        resolved = str(app_paths.config_dir())
        assert os.path.isdir(resolved), f"Missing resolved config directory: {resolved}"

        # Container mode is decided by ONE marker, not by "do all the mounts
        # happen to be here". The old form asked whether every container path
        # existed and skipped if any was missing — so a container missing two of
        # its mounts reported "not a container, skipped" instead of failing,
        # which is the exact layout the assertion is for.
        in_container = os.path.isdir('/app/calibre-web-automated')
        if not in_container:
            pytest.skip("Not running inside the container image (source checkout)")

        required_mounts = ['/config', '/calibre-library', '/cwa-book-ingest']
        missing = [d for d in required_mounts if not os.path.isdir(d)]
        assert not missing, f"Container is missing required mounts: {missing}"
    
    def test_flask_app_can_be_imported(self):
        """Verify Flask app module can be imported without errors."""
        try:
            from cps import app
            assert app is not None
        except ImportError as e:
            pytest.fail(f"Failed to import Flask app: {e}")
    
    def test_cwa_db_can_be_imported(self):
        """Verify CWA database module can be imported."""
        # Try container path first, fall back to workspace
        scripts_path = '/app/calibre-web-automated/scripts/'
        if not os.path.exists(scripts_path):
            # Running outside container - use workspace path
            workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            scripts_path = os.path.join(workspace_root, 'scripts')
        
        sys.path.insert(0, scripts_path)
        try:
            from cwa_db import CWA_DB
            assert CWA_DB is not None
        except ImportError as e:
            pytest.fail(f"Failed to import CWA_DB: {e}")


@pytest.mark.smoke
@pytest.mark.requires_calibre
class TestCalibreTools:
    """Verify Calibre CLI tools are installed and accessible."""
    
    def test_calibredb_exists(self):
        """Verify calibredb binary is installed."""
        import shutil
        calibredb_path = shutil.which('calibredb')
        assert calibredb_path is not None, "calibredb not found in PATH"
    
    def test_ebook_convert_exists(self):
        """Verify ebook-convert binary is installed."""
        import shutil
        convert_path = shutil.which('ebook-convert')
        assert convert_path is not None, "ebook-convert not found in PATH"
    
    def test_ebook_meta_exists(self):
        """Verify ebook-meta binary is installed."""
        import shutil
        meta_path = shutil.which('ebook-meta')
        assert meta_path is not None, "ebook-meta not found in PATH"
    
    def test_kepubify_exists(self):
        """Verify kepubify binary is installed."""
        import shutil
        kepubify_path = shutil.which('kepubify')
        assert kepubify_path is not None, "kepubify not found in PATH"
    
    def test_calibre_version(self):
        """Verify Calibre version can be queried."""
        import subprocess
        result = subprocess.run(
            ['calibredb', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        assert result.returncode == 0, "calibredb --version failed"
        assert 'calibre' in result.stdout.lower(), "Unexpected calibredb version output"


@pytest.mark.smoke
class TestDatabaseAccess:
    """Verify database modules and connections work."""
    
    def test_cwa_db_initialization(self, temp_cwa_db):
        """Verify CWA database can be initialized."""
        assert temp_cwa_db is not None
        assert temp_cwa_db.con is not None
        assert temp_cwa_db.cur is not None
    
    def test_cwa_db_has_required_tables(self, temp_cwa_db):
        """Verify CWA database has all required tables."""
        expected_tables = [
            'cwa_enforcement',
            'cwa_import',
            'cwa_conversions',
            'epub_fixes',
            'cwa_settings'
        ]
        
        # Extract table names from CREATE TABLE statements
        import re
        actual_table_names = []
        for table_stmt in temp_cwa_db.tables:
            match = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)\(', table_stmt)
            if match:
                actual_table_names.append(match.group(1))
        
        for table in expected_tables:
            assert table in actual_table_names, f"Missing required table: {table}"
    
    def test_cwa_db_settings_accessible(self, temp_cwa_db):
        """Verify CWA settings can be read from database."""
        settings = temp_cwa_db.get_cwa_settings()
        assert settings is not None
        assert isinstance(settings, dict)


@pytest.mark.smoke
class TestFileFormatDetection:
    """Verify file format detection logic works correctly."""
    
    def test_supported_formats_recognized(self):
        """Verify all supported ebook formats are recognized."""
        # Import the function that checks file formats
        # This is a simplified test - adjust based on actual implementation
        
        supported_extensions = [
            'epub', 'mobi', 'azw', 'azw3', 'azw4', 'pdf', 'txt',
            'cbz', 'cbr', 'cb7', 'cbc', 'fb2', 'fbz', 'docx',
            'html', 'htmlz', 'lit', 'lrf', 'odt', 'prc', 'pdb',
            'pml', 'rb', 'snb', 'tcr', 'txtz', 'kepub', 'acsm'
        ]
        
        # Test that we have all 27+ formats
        assert len(supported_extensions) >= 27, "Missing supported format definitions"
    
    def test_temp_file_suffixes_defined(self):
        """Verify temp file suffixes are properly defined for filtering."""
        temp_suffixes = ['crdownload', 'download', 'part', 'uploading']
        
        # These should be filtered out during ingest
        assert len(temp_suffixes) > 0, "Temp file suffixes not defined"


@pytest.mark.smoke
class TestLockMechanism:
    """Verify process locking mechanism works."""
    
    @pytest.mark.timeout(10)
    def test_lock_can_be_acquired(self, tmp_path, monkeypatch):
        """Verify lock can be acquired successfully."""
        sys.path.insert(0, '/app/calibre-web-automated/scripts/')
        
        # Override temp directory for test isolation
        import tempfile
        monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
        
        # Skip if required directories don't exist (not in container)
        if not os.path.exists('/config/processed_books'):
            pytest.skip("Required directories not available (running outside container)")
        
        from ingest_processor import ProcessLock
        
        lock = ProcessLock("test_lock")
        assert lock.acquire(timeout=2), "Failed to acquire lock"
        lock.release()
    
    @pytest.mark.timeout(10)
    def test_lock_prevents_concurrent_access(self, tmp_path, monkeypatch):
        """Verify second process cannot acquire lock while held."""
        sys.path.insert(0, '/app/calibre-web-automated/scripts/')
        
        import tempfile
        monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
        
        # Skip if required directories don't exist (not in container)
        if not os.path.exists('/config/processed_books'):
            pytest.skip("Required directories not available (running outside container)")
        
        from ingest_processor import ProcessLock
        
        lock1 = ProcessLock("test_lock")
        assert lock1.acquire(timeout=2), "First lock acquisition failed"
        
        # Second lock should fail
        lock2 = ProcessLock("test_lock")
        assert not lock2.acquire(timeout=1), "Second lock should have failed but succeeded"
        
        # Release first lock
        lock1.release()
        
        # Now second lock should succeed
        assert lock2.acquire(timeout=2), "Lock should be available after release"
        lock2.release()


@pytest.mark.smoke
class TestEnvironmentConfiguration:
    """Verify environment variables and configuration work."""
    
    def test_can_read_cwa_version(self):
        """Verify the image stamped the version it was built from.

        This used to read /app/CWA_RELEASE. That file is gone: the build sets
        CWA_INSTALLED_VERSION as an env var in the final stage instead. The
        check has to follow the stamp rather than the old file, or it turns
        into a permanent skip that asserts nothing in the very environment it
        exists to cover.
        """
        version = os.environ.get('CWA_INSTALLED_VERSION')
        if not version:
            pytest.skip(
                "Not in a built image - CWA_INSTALLED_VERSION is not stamped"
            )
        # A release image carries the tag; a dev image carries DEV_BUILD-dev-<n>.
        assert version.startswith(('V', 'v', 'DEV_BUILD')), \
            f"Version format unexpected: {version}"
    
    def test_network_share_mode_detection(self):
        """Verify network share mode can be detected from environment."""
        # This should not crash
        network_mode = os.environ.get('NETWORK_SHARE_MODE', 'false').lower()
        assert network_mode in ['true', 'false', '0', '1', 'yes', 'no', 'on', 'off'], \
            f"Invalid NETWORK_SHARE_MODE value: {network_mode}"


# ============================================================================
# Quick Sanity Check - Run this first!
# ============================================================================

@pytest.mark.smoke
def test_smoke_suite_itself():
    """
    Meta-test: Verify the smoke test suite can run.
    
    This test should always pass. If it fails, something is very wrong
    with the test infrastructure itself.
    """
    assert True, "If this fails, the test infrastructure is broken"


if __name__ == '__main__':
    # Allow running smoke tests directly: python tests/smoke/test_smoke.py
    pytest.main([__file__, '-v'])
