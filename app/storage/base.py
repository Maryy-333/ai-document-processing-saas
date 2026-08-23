"""
StorageService abstraction.

Phase 4 provides exactly one implementation (LocalStorageService, local
filesystem). No cloud storage backend is implemented or approved yet — this
Protocol exists so a future backend (approved separately, per the Phase 1
architecture rule) can be swapped in without touching calling code.
"""

from typing import BinaryIO, Protocol


class StorageService(Protocol):
    def save(self, key: str, data: BinaryIO) -> None:
        """Persist bytes under the given storage key. Overwrites if it exists."""
        ...

    def read(self, key: str) -> bytes:
        """Read back the bytes stored under the given key. Raises if missing."""
        ...

    def exists(self, key: str) -> bool:
        """Whether something is stored under the given key."""
        ...

    def delete(self, key: str) -> None:
        """
        Best-effort delete of the given key. Must NOT raise if the key does
        not exist — callers (e.g. transaction-safety cleanup) rely on this
        being safe to call even when they're not certain the write completed.
        """
        ...
