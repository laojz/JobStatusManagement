from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jobs_status_manager.release_manifest import (
    ReleaseManifest,
    validate_release_manifest,
)


def _manifest_data() -> dict[str, str | datetime | dict[str, str]]:
    return {
        "application_version": "0.1.0",
        "source_commit": "source-commit-under-test",
        "python_version": "3.12.0",
        "uv_version": "0.9.0",
        "sdk_versions": {"qq-botpy-sdk": "2.0.4"},
        "lockfile_digest": "lockfile-digest-under-test",
        "schema_head": "0008_qq_reply_targets",
        "build_time": datetime(2026, 9, 13, 12, tzinfo=UTC),
        "artifact_checksum": "artifact-checksum-under-test",
        "evidence_index": "docs/evidence/index.md",
        "runbook_version": "operations-v1",
    }


def test_manifest_requires_all_provenance_fields() -> None:
    data = _manifest_data()
    del data["source_commit"]

    with pytest.raises(ValidationError, match="source_commit"):
        ReleaseManifest.model_validate(data)


def test_manifest_rejects_blank_required_fields() -> None:
    data = _manifest_data()
    data["lockfile_digest"] = "  "

    with pytest.raises(ValidationError, match="lockfile_digest"):
        ReleaseManifest.model_validate(data)


def test_manifest_rejects_schema_head_mismatch() -> None:
    manifest = ReleaseManifest.model_validate(_manifest_data())

    with pytest.raises(ValueError, match="schema head"):
        validate_release_manifest(manifest, expected_schema_head="0007_previous_head")


def test_manifest_accepts_valid_provenance_and_expected_schema_head() -> None:
    manifest = ReleaseManifest.model_validate(_manifest_data())

    validated = validate_release_manifest(
        manifest,
        expected_schema_head="0008_qq_reply_targets",
    )

    assert validated == manifest
