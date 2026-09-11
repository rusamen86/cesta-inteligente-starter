from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from .models import ReceiptImageEvidence


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_image_evidence(paths: list[str | Path]) -> list[ReceiptImageEvidence]:
    evidence: list[ReceiptImageEvidence] = []
    for ordinal, raw_path in enumerate(paths):
        source = Path(raw_path)
        path = source.resolve()
        file_hash = sha256_file(path)
        if not source.is_absolute():
            storage_ref = source.as_posix()
        else:
            try:
                storage_ref = path.relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                storage_ref = f"objects/{file_hash[:2]}/{file_hash}{path.suffix.lower()}"
        evidence.append(
            ReceiptImageEvidence(
                ordinal=ordinal,
                sha256=file_hash,
                storage_id=f"sha256:{file_hash}",
                storage_ref=storage_ref,
                mime_type=mimetypes.guess_type(path.name)[0],
            )
        )
    return evidence


def stable_receipt_hash(images: list[ReceiptImageEvidence]) -> str:
    """Hash a set of pages without making upload order part of identity."""
    if not images:
        raise ValueError("A receipt requires at least one source image or PDF")
    digest = hashlib.sha256()
    digest.update(b"cesta-receipt-v1\0")
    for image_hash in sorted(image.sha256 for image in images):
        digest.update(image_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()
