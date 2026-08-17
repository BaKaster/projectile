from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import anyio
from fastapi import UploadFile


class FileTooLargeError(ValueError):
    def __init__(self, filename: str, max_bytes: int) -> None:
        super().__init__(f"File '{filename}' exceeds {max_bytes} bytes")
        self.filename = filename
        self.max_bytes = max_bytes


@dataclass(slots=True)
class StagedUpload:
    temp_path: Path
    original_filename: str
    stored_filename: str
    media_type: str
    size_bytes: int
    checksum_sha256: str


@dataclass(slots=True)
class PersistedFile:
    absolute_path: Path
    storage_uri: str


_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f<>:\"/\\|?*]+")


def safe_filename(filename: str | None) -> str:
    raw = unicodedata.normalize("NFKC", filename or "upload.bin")
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", basename).strip(" .")
    if not cleaned:
        cleaned = "upload.bin"
    stem, suffix = os.path.splitext(cleaned)
    suffix = suffix[:32]
    max_stem_length = max(1, 220 - len(suffix))
    return f"{stem[:max_stem_length]}{suffix}"


class LocalFileStorage:
    def __init__(self, root: Path, max_bytes: int, chunk_size: int) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size
        self.staging_root = self.root / ".staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    async def stage(self, upload: UploadFile) -> StagedUpload:
        stored_name = safe_filename(upload.filename)
        original_name = safe_filename(upload.filename)
        temp_path = self.staging_root / f"{uuid.uuid4()}.part"
        digest = hashlib.sha256()
        size = 0

        try:
            async with await anyio.open_file(temp_path, "wb") as output:
                while chunk := await upload.read(self.chunk_size):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FileTooLargeError(original_name, self.max_bytes)
                    digest.update(chunk)
                    await output.write(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        return StagedUpload(
            temp_path=temp_path,
            original_filename=original_name,
            stored_filename=stored_name,
            media_type=upload.content_type or "application/octet-stream",
            size_bytes=size,
            checksum_sha256=digest.hexdigest(),
        )

    def persist(
        self,
        staged: StagedUpload,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        version: int,
    ) -> PersistedFile:
        relative = (
            PurePosixPath("documents")
            / str(project_id)
            / str(document_id)
            / f"v{version}"
            / staged.stored_filename
        )
        destination = self.root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged.temp_path, destination)
        return PersistedFile(absolute_path=destination, storage_uri=relative.as_posix())

    @staticmethod
    def discard(staged: StagedUpload) -> None:
        staged.temp_path.unlink(missing_ok=True)

    @staticmethod
    def remove_persisted(persisted: PersistedFile) -> None:
        persisted.absolute_path.unlink(missing_ok=True)
