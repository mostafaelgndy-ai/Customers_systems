"""Safe file storage for uploads (PRD §8.1 security requirements)."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import APIError

# Magic-byte sniffing for binary content disguised as CSV.
_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"PK\x03\x04",  # zip / xlsx
    b"\x89PNG",
    b"%PDF",
    b"\x1f\x8b",  # gzip
    b"\xff\xd8\xff",  # jpeg
    b"Rar!",
)


def sanitize_display_name(name: str) -> str:
    """Sanitized original filename for display/audit only — never used for storage paths."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "upload")
    return stem[-64:]


def save_upload(*, data: bytes, original_filename: str) -> tuple[str, int, str, list[str]]:
    """Persist an upload; return (relative_path, size, sha256, parsed_headers).

    Storage paths are generated server-side (no user-controlled path
    components) and live under UPLOAD_DIR, outside any web root.
    Raises APIError (413/415) on size/content violations.
    """
    safe_name = sanitize_display_name(original_filename)
    ext = Path(safe_name).suffix.lower()

    if len(data) > settings.max_upload_bytes:
        raise APIError(
            f"Upload exceeds the {settings.max_upload_mb} MB limit.",
            413,
            code="PAYLOAD_TOO_LARGE",
        )
    if data.count(b"\n") == 0 and len(data) < 4:
        raise APIError("Empty upload.", 415, code="UNSUPPORTED_MEDIA_TYPE")
    if ext and ext not in settings.allowed_upload_extensions:
        raise APIError(
            "Only CSV files are accepted (.csv).",
            415,
            code="UNSUPPORTED_MEDIA_TYPE",
        )
    if _looks_like_text(data):
        headers = _parse_header_line(data)
    else:
        raise APIError(
            "The uploaded file is not valid CSV text.",
            415,
            code="UNSUPPORTED_MEDIA_TYPE",
        )

    file_id = uuid.uuid4()
    relative = Path("datasets") / f"{file_id}{ext}"
    target_dir = Path(settings.upload_dir) / relative.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    (Path(settings.upload_dir) / relative).write_bytes(data)

    return str(relative), len(data), hashlib.sha256(data).hexdigest(), headers


def _looks_like_text(data: bytes) -> bool:
    for sig in _BINARY_SIGNATURES:
        if data[:8].startswith(sig):
            return False
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        try:
            data[:4096].decode("utf-16")
        except UnicodeDecodeError:
            return False
    return True


def _parse_header_line(data: bytes) -> list[str]:
    """Return the raw first CSV row for mapping suggestions."""
    head = data[: 64 * 1024].decode("utf-8", errors="replace")
    lines = head.splitlines()
    if not lines:
        return []
    return [c.strip() for c in lines[0].split(",")]


def resolve_storage_path(relative_path: str) -> Path:
    """Resolve a stored relative path, refusing path traversal."""
    root = Path(settings.upload_dir).resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise APIError("Invalid file reference.", 404, code="NOT_FOUND")
    return candidate


def read_file_bytes(relative_path: str) -> bytes:
    target = resolve_storage_path(relative_path)
    if not target.exists():
        raise APIError("Stored file is missing.", 404, code="NOT_FOUND")
    return target.read_bytes()


def delete_file(relative_path: str) -> None:
    target = resolve_storage_path(relative_path)
    if target.exists():
        target.unlink(missing_ok=True)
