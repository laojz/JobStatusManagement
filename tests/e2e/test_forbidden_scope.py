"""Phase 5 negative scope assertions."""

from pathlib import Path


def test_phase_five_keeps_rag_local_and_credential_free() -> None:
    source_root = Path(__file__).resolve().parents[2]
    source_text = "\n".join(
        path.read_text()
        for path in (
            *source_root.joinpath("src").rglob("*.py"),
            *source_root.joinpath("migrations/versions").glob("*.py"),
        )
    ).lower()
    assert "updateapplicationstatus" in source_text
    assert "addknowledge" in source_text
    assert "removeknowledge" in source_text
    assert "searchknowledge" in source_text
    assert "chromadb" in source_text
    assert "redis" not in source_text
    assert "celery" not in source_text
    assert "embed_document" not in source_text
    assert "mails" in source_text
    assert "notifications" in source_text
