#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Simple test to verify basic EPUB ingest works.
"""

import pytest
import time
import subprocess

from tests.conftest import volume_copy

# Drives a real container via the cwa_container fixture, so it belongs to the
# Integration job, not the Fast Tests gate. It sat at tests/ root with no
# marker, where the fast gate deselected it and the integration job's explicit
# tests/docker/ tests/integration/ paths never named it — it ran nowhere (#1105).
pytestmark = pytest.mark.docker_integration


def test_simple_epub_ingest(cwa_container, ingest_folder, container_name, tmp_path):
    """
    Drop an EPUB and verify it gets processed.
    """
    print("\n" + "=" * 80)
    print("🧪 SIMPLE EPUB INGEST TEST")
    print("=" * 80)

    # Create a minimal EPUB
    from tests.fixtures.generate_synthetic import create_minimal_epub

    epub_path = tmp_path / "simple_test.epub"
    create_minimal_epub(epub_path)
    print(f"✓ Created EPUB: {epub_path} ({epub_path.stat().st_size} bytes)")

    # volume_copy + `/` + .exists() are the pair that work in both bind and
    # auto-fallback volume mode. This test used to call ingest_folder.copy_to()
    # / .file_exists(), which only exist on VolumePath — so it could only ever
    # have passed in volume mode, and AUTO_DOCKER_VOLUMES defaults to False.
    # Nobody noticed because the test errored at fixture setup instead (#1201).
    dest_file = ingest_folder / "simple_test.epub"
    volume_copy(epub_path, dest_file)
    print(f"✓ Copied into ingest folder: {dest_file}")
    print(f"✓ File exists in ingest folder: {dest_file.exists()}")

    # Check what the container sees
    print("\n🔍 Checking container's view of /cwa-book-ingest:")
    result = subprocess.run(
        ["docker", "exec", container_name, "ls", "-la", "/cwa-book-ingest"],
        capture_output=True, text=True
    )
    print(result.stdout)

    # Wait and watch
    print("\n⏳ Waiting for file to be processed...")
    for i in range(30):
        time.sleep(2)
        exists = dest_file.exists()
        print(f"[{(i + 1) * 2}s] File still exists: {exists}")

        if not exists:
            print("✅ File was consumed!")
            break

        # Check container view periodically
        if i % 5 == 0:
            result = subprocess.run(
                ["docker", "exec", container_name, "ls", "-la", "/cwa-book-ingest"],
                capture_output=True, text=True
            )
            print(f"Container view:\n{result.stdout}")
    else:
        print("❌ File still exists after 60 seconds")

        # Check container logs
        print("\n📋 Container logs:")
        result = subprocess.run(
            ["docker", "logs", "--tail", "50", container_name],
            capture_output=True, text=True
        )
        print(result.stdout)

        pytest.fail("File was not processed")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
