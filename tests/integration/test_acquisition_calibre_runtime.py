# SPDX-License-Identifier: GPL-3.0-or-later
"""Acquisition correctness against the candidate image's actual Calibre runtime."""
import json
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.docker_integration
ROOT = Path(__file__).resolve().parents[2]


def test_real_acquisition_retention_reinspection_and_durable_receipt(
    cwa_container, container_name
):
    # Copy test inputs only. Product modules must be baked into the CI image.
    files = {
        ROOT
        / "tests/integration/acquisition_calibre_runtime_probe.py": "/tmp/acquisition-runtime-probe.py",
        ROOT
        / "tests/integration/calibre_ingest_runtime_probe.py": "/tmp/acquisition-base-probe.py",
        ROOT
        / "tests/fixtures/sample_books/test_minimal_valid.epub": "/tmp/acquisition-fixture.epub",
    }
    for source, target in files.items():
        subprocess.run(
            ["docker", "cp", str(source), container_name + ":" + target], check=True
        )
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "cwa-as-abc",
            "python3",
            "/tmp/acquisition-runtime-probe.py",
            "--fixture",
            "/tmp/acquisition-fixture.epub",
            "--setup-probe",
            "/tmp/acquisition-base-probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    records = [
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("CWNG_ACQUISITION_RUNTIME=")
    ]
    assert len(records) == 1, result.stdout
    proof = json.loads(records[0])
    assert proof["calibre_version"].startswith("calibre-debug (calibre ")
    assert proof["helper"]["retained"]["disposition"] == "existing_retained"
    assert proof["helper"]["removed_reacquire"]["status"] == "imported"
    assert (
        proof["helper"]["replacement_reinspect"]["disposition"] == "imported"
    )
    assert proof["helper"]["distinct"]["disposition"] == "imported"
    assert proof["helper"]["annotations_preserved"]
    assert [case["existing_language"] for case in proof["languages"]] == [["eng"], []]
    assert all(case["annotations_preserved"] for case in proof["languages"])
    assert proof["boundary"]["helper_and_receipt_agree"]
    assert proof["boundary"]["external_bytes_unchanged"]
    assert proof["boundary"]["annotations_preserved"]
    assert proof["helper"]["forged_identifier_ignored"]["book_ids"] != [1]
    assert proof["receipt"]["real_ub_schema"]
    assert proof["receipt"]["receipt_failure_rolled_back_membership"]
    assert proof["receipt"]["source_retained"]
    assert proof["receipt"]["acknowledged"]
    assert proof["receipt"]["membership_count"] == proof["receipt"]["book_count"] == 1
    assert proof["receipt"]["helper_calls"] == ["import"]
    assert proof["receipt"]["retry_reimports"] is False


def test_owned_opds_worker_full_processor_conversion_and_receipt(
    cwa_container, container_name
):
    files = {
        ROOT
        / "tests/integration/acquisition_full_runtime_probe.py": "/tmp/acquisition_full_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_mobi_runtime_probe.py": "/tmp/acquisition_mobi_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_calibre_runtime_probe.py": "/tmp/acquisition_calibre_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_bundle_runtime_probe.py": "/tmp/acquisition_bundle_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_torrent_metadata_runtime_probe.py": "/tmp/acquisition_torrent_metadata_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_opds_publication_runtime_probe.py": "/tmp/acquisition_opds_publication_runtime_probe.py",
        ROOT
        / "tests/fixtures/sample_books/test_minimal_valid.epub": "/tmp/acquisition-full-fixture.epub",
        ROOT
        / "tests/fixtures/sample_books/test_original_direct.mobi": "/tmp/acquisition-original.mobi",
        ROOT
        / "tests/fixtures/sample_books/test_original_direct_uncompressed.mobi": "/tmp/acquisition-original-uncompressed.mobi",
    }
    for source, target in files.items():
        subprocess.run(
            ["docker", "cp", str(source), container_name + ":" + target], check=True
        )
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "cwa-as-abc",
            "python3",
            "/tmp/acquisition_full_runtime_probe.py",
            "--fixture",
            "/tmp/acquisition-full-fixture.epub",
            "--mobi-fixture",
            "/tmp/acquisition-original.mobi",
            "--mobi-uncompressed-fixture",
            "/tmp/acquisition-original-uncompressed.mobi",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    records = [
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("CWNG_ACQUISITION_FULL_RUNTIME=")
    ]
    assert len(records) == 1, result.stdout
    proof = json.loads(records[0])
    assert proof["http_gets"] == [
        "/catalog",
        "/patch.epub",
        "/duplicate.epub",
        "/convert.epub",
        "/direct.pdf",
        "/repacked.epub",
        "/catalog",
        "/repacked.epub",
    ]
    assert [case["format"] for case in proof["results"]] == [
        "epub",
        "epub",
        "kepub",
        "pdf",
        "epub",
    ]
    repacked = proof["results"][4]
    assert repacked["receipt_retry"] and repacked["book_ids"] == proof["results"][0]["book_ids"]
    assert repacked["source_sha256"] != repacked["imported_sha256"] == proof["results"][0]["imported_sha256"]
    assert all(proof["repackaging"][key] for key in ("zip_only_equal", "original_bytes_and_annotations", "app_reading_state", "receipt_retry_no_reimport", "other_owner_membership", "distinct_owned_receipts"))
    assert proof["results"][0]["receipt_retry"]
    mobi = proof['mobi']
    assert all(mobi[key] for key in ('receipt_fault_recovery', 'owned_cleanup', 'cross_owner_refused',
                                    'independent_owner_receipts', 'no_cross_format_metadata_overwrite', 'full_processor_subprocess'))
    assert [case['actual_format'] for case in mobi['results']] == ['EPUB', 'MOBI', 'MOBI', 'EPUB']
    assert mobi['results'][0]['book_id'] == mobi['results'][3]['book_id'] != mobi['results'][1]['book_id']
    assert mobi['results'][1]['book_id'] == mobi['results'][2]['book_id']
    assert mobi['results'][1]['owner'] != mobi['results'][2]['owner']
    assert mobi['results'][1]['source_sha256'] == mobi['results'][1]['imported_sha256']
    assert mobi['results'][0]['source_sha256'] != mobi['results'][0]['imported_sha256']
    assert all(case["source_and_private_cleanup"] for case in proof["results"])
    bundle = proof["bundle"]
    assert bundle["remote_submissions"] == 1 and bundle["source_files_unchanged"]
    assert bundle["waiting_not_polled"] and bundle["private_manifests"] and bundle["real_sab_http"]
    assert len(bundle["outcomes"]) == 3
    assert bundle["outcomes"][0]["book_ids"] != bundle["outcomes"][1]["book_ids"]
    assert bundle["outcomes"][2]["book_ids"] == bundle["outcomes"][0]["book_ids"]
    detail = proof["opds_publication"]
    assert detail["explicit_detail_read"] and detail["cross_account_read_refused"]
    assert detail["no_loan_or_purchase_get"] and detail["original_edition_preserved"]
    assert detail["source_unchanged"] and detail["private_cleanup"] and detail["full_processor_subprocess"]
    assert detail["saved_display_locale"] == "fr_CA" and detail["edition_language"] == "en"
    entry = proof["opds1_entry"]
    assert entry["protocol"] == "opds1" and entry["displayed_title"] == "Original OPDS1 entry edition"
    assert entry["explicit_detail_read"] and entry["cross_account_read_refused"]
    assert entry["no_loan_or_purchase_get"] and entry["original_edition_preserved"]
    assert entry["source_unchanged"] and entry["private_cleanup"] and entry["full_processor_subprocess"]
    assert entry["source_sha256"] == entry["imported_sha256"] and entry["edition_language"] == "en"
    assert entry["book_ids"] != detail["book_ids"]
    metadata = proof["torrent_metadata"]
    assert metadata["loopback_transmission_rpc"] and metadata["full_processor_subprocess"]
    assert [case["case"] for case in metadata["outcomes"]] == ["single", "multi"]
    assert all(case["exact_descriptor_submitted"] and case["source_files_unchanged"]
        and case["remote_submissions"] == 1 for case in metadata["outcomes"])
