"""Provider-neutral release manifest contract.

Build tooling should populate this model after it has resolved the application,
runtime, SDK, lockfile, migration, artifact, evidence, and runbook inputs. This
module only parses and validates that data; it does not build or publish an
artifact.
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


@dataclass(frozen=True, slots=True)
class SchemaHeadMismatchError(ValueError):
    """Raised when a manifest is not for the expected migration head."""

    actual_schema_head: str
    expected_schema_head: str

    def __str__(self) -> str:
        """Describe the actual and expected migration heads."""
        return (
            "release manifest schema head does not match expected head: "
            f"actual={self.actual_schema_head!r} expected={self.expected_schema_head!r}"
        )


@dataclass(frozen=True, slots=True)
class ReleaseManifestValidationError(ValueError):
    """Raised when a manifest value violates its field contract."""

    field_name: str
    reason: str

    def __str__(self) -> str:
        """Describe the invalid manifest field."""
        return f"release manifest field {self.field_name!r} {self.reason}"


class ReleaseManifest(BaseModel):
    """Immutable provenance fields required for a future release artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    application_version: str
    source_commit: str
    python_version: str
    uv_version: str
    sdk_versions: dict[str, str]
    lockfile_digest: str
    schema_head: str
    build_time: datetime
    artifact_checksum: str
    evidence_index: str
    runbook_version: str

    @field_validator(
        "application_version",
        "source_commit",
        "python_version",
        "uv_version",
        "lockfile_digest",
        "schema_head",
        "artifact_checksum",
        "evidence_index",
        "runbook_version",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject missing semantic values represented as blank text."""
        if not value.strip():
            field_name = "text field"
            reason = "must not be blank"
            raise ReleaseManifestValidationError(field_name, reason)
        return value

    @field_validator("sdk_versions")
    @classmethod
    def reject_blank_sdk_versions(cls, value: dict[str, str]) -> dict[str, str]:
        """Require at least one named SDK version with non-blank values."""
        if not value:
            field_name = "sdk_versions"
            reason = "must contain at least one SDK"
            raise ReleaseManifestValidationError(field_name, reason)
        if any(not name.strip() or not version.strip() for name, version in value.items()):
            field_name = "sdk_versions"
            reason = "names and versions must not be blank"
            raise ReleaseManifestValidationError(field_name, reason)
        return value


def validate_release_manifest(
    manifest: ReleaseManifest,
    *,
    expected_schema_head: str,
) -> ReleaseManifest:
    """Validate a parsed manifest against the explicitly supplied schema head."""
    if not expected_schema_head.strip():
        field_name = "expected_schema_head"
        reason = "must not be blank"
        raise ReleaseManifestValidationError(field_name, reason)
    if manifest.schema_head != expected_schema_head:
        raise SchemaHeadMismatchError(
            actual_schema_head=manifest.schema_head,
            expected_schema_head=expected_schema_head,
        )
    return manifest
