from __future__ import annotations

import hashlib
from pathlib import Path


class LocalResumeStorage:
    """Filesystem storage behind a small interface so S3 can replace it later."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def write(self, key: str, content: bytes) -> tuple[str, int, str]:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError("Storage key escapes the configured root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target), len(content), hashlib.sha256(content).hexdigest()

    def read(self, key: str) -> bytes:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError("Storage key escapes the configured root")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError("Storage key escapes the configured root")
        target.unlink(missing_ok=True)
