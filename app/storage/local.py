"""
LocalStorageService: filesystem-backed StorageService implementation.

Storage keys reaching this class are always server-generated (see
app/processing/validation.py / document_service.py — never derived from
client-controlled input such as the original filename). Even so, this class
enforces path containment itself as defense in depth: it never trusts that a
key is safe just because the caller says so.
"""

from pathlib import Path

from app.core.exceptions import ProcessingError


class StorageKeyError(ProcessingError):
    """A storage key resolved outside the configured storage root."""

    message = "Invalid storage key."


class LocalStorageService:
    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Reject absolute keys / drive-qualified keys outright before joining,
        # since Path(root) / "/etc/passwd" would otherwise just become
        # "/etc/passwd" on POSIX (join with an absolute path discards root).
        if Path(key).is_absolute():
            raise StorageKeyError()

        candidate = (self._root / key).resolve()

        # Containment check: the resolved path must sit inside the storage
        # root even after resolving any ".." segments or symlinks.
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise StorageKeyError() from exc

        return candidate

    def save(self, key: str, data) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data.read() if hasattr(data, "read") else data)

    def read(self, key: str) -> bytes:
        path = self._resolve(key)
        with open(path, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        try:
            path = self._resolve(key)
        except StorageKeyError:
            return False
        return path.is_file()

    def delete(self, key: str) -> None:
        try:
            path = self._resolve(key)
        except StorageKeyError:
            return
        path.unlink(missing_ok=True)
