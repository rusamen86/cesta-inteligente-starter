from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .database import connection, initialize_database


ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
MAX_MEDIA_BYTES = 25 * 1024 * 1024


class UnsafeMediaReference(ValueError):
    pass


@dataclass(frozen=True)
class StoredMedia:
    storage_id: str
    sha256: str
    storage_ref: str
    mime_type: str
    byte_size: int
    path: Path


def _sha256_descriptor(handle) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


class MediaStore:
    """Content-addressed media store with root-bounded, symlink-safe ingestion."""

    def __init__(self, db_path: str | Path, *, inbound_root: str | Path, object_root: str | Path) -> None:
        self.db_path = Path(db_path)
        self.inbound_root = Path(inbound_root).resolve(strict=True)
        self.object_root = Path(object_root).resolve()
        self.object_root.mkdir(parents=True, exist_ok=True)
        initialize_database(self.db_path)

    def _trusted_source(self, relative_ref: str) -> Path:
        pure = PurePosixPath(relative_ref)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise UnsafeMediaReference("media reference must be a relative path without traversal")
        raw = self.inbound_root.joinpath(*pure.parts)
        cursor = self.inbound_root
        for part in pure.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise UnsafeMediaReference("symlink media paths are not allowed")
        try:
            resolved = raw.resolve(strict=True)
            resolved.relative_to(self.inbound_root)
        except (FileNotFoundError, ValueError) as exc:
            raise UnsafeMediaReference("media path is outside the inbound root") from exc
        if not resolved.is_file():
            raise UnsafeMediaReference("media must be a regular file")
        return resolved

    def register_trusted_inbound(self, relative_ref: str, *, mime_type: str | None = None) -> StoredMedia:
        """Trusted ingress adapter API; never exposed directly as an agent tool."""
        source = self._trusted_source(relative_ref)
        detected = mime_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if detected not in ALLOWED_MIME_TYPES:
            raise UnsafeMediaReference(f"unsupported media type: {detected}")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        try:
            source_stat = os.fstat(descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise UnsafeMediaReference("media must be a regular file")
            if source_stat.st_size > MAX_MEDIA_BYTES:
                raise UnsafeMediaReference("media exceeds the 25 MiB limit")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                file_hash, byte_size = _sha256_descriptor(handle)
                handle.seek(0)
                suffix = source.suffix.lower() or mimetypes.guess_extension(detected) or ".bin"
                storage_ref = f"objects/{file_hash[:2]}/{file_hash}{suffix}"
                destination = self.object_root / file_hash[:2] / f"{file_hash}{suffix}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
                        temp_path = Path(temp.name)
                        shutil.copyfileobj(handle, temp)
                    os.chmod(temp_path, 0o600)
                    os.replace(temp_path, destination)
        finally:
            os.close(descriptor)

        storage_id = f"sha256:{file_hash}"
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO media_objects(
                    storage_id, sha256, storage_ref, mime_type, byte_size
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (storage_id, file_hash, storage_ref, detected, byte_size),
            )
        return self.resolve(storage_id)

    def resolve(self, storage_id: str) -> StoredMedia:
        if not storage_id.startswith("sha256:") or len(storage_id) != 71:
            raise UnsafeMediaReference("invalid storage id")
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT storage_id, sha256, storage_ref, mime_type, byte_size FROM media_objects WHERE storage_id = ?",
                (storage_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown media object: {storage_id}")
        relative = PurePosixPath(str(row["storage_ref"]))
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("objects",):
            raise UnsafeMediaReference("unsafe stored media reference")
        path = self.object_root.joinpath(*relative.parts[1:]).resolve(strict=True)
        try:
            path.relative_to(self.object_root)
        except ValueError as exc:
            raise UnsafeMediaReference("stored media escaped object root") from exc
        if path.is_symlink() or not path.is_file():
            raise UnsafeMediaReference("stored media is not a regular file")
        return StoredMedia(
            storage_id=str(row["storage_id"]),
            sha256=str(row["sha256"]),
            storage_ref=str(row["storage_ref"]),
            mime_type=str(row["mime_type"]),
            byte_size=int(row["byte_size"]),
            path=path,
        )
