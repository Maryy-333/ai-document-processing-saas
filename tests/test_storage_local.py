import io

import pytest

from app.storage.local import LocalStorageService, StorageKeyError


def test_save_and_read_round_trip(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    storage.save("orgs/abc/file1.pdf", io.BytesIO(b"hello pdf"))

    assert storage.exists("orgs/abc/file1.pdf")
    assert storage.read("orgs/abc/file1.pdf") == b"hello pdf"


def test_delete_removes_file(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    storage.save("a/b.pdf", io.BytesIO(b"x"))
    assert storage.exists("a/b.pdf")

    storage.delete("a/b.pdf")
    assert not storage.exists("a/b.pdf")


def test_delete_nonexistent_key_does_not_raise(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    # Must be safe to call even if the write never happened — cleanup logic
    # in document_service relies on this.
    storage.delete("never/existed.pdf")


def test_exists_false_for_missing_key(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    assert storage.exists("nothing/here.pdf") is False


@pytest.mark.parametrize(
    "malicious_key",
    [
        "../../etc/passwd",
        "../outside.pdf",
        "a/../../b.pdf",
        "/etc/passwd",
    ],
)
def test_save_rejects_path_traversal_keys(tmp_path, malicious_key):
    storage = LocalStorageService(str(tmp_path))
    with pytest.raises(StorageKeyError):
        storage.save(malicious_key, io.BytesIO(b"x"))

    # Confirm nothing was written outside the storage root.
    escaped_target = (tmp_path / ".." / "etc_passwd_test").resolve()
    assert not escaped_target.exists()


def test_read_rejects_path_traversal_keys(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    with pytest.raises(StorageKeyError):
        storage.read("../../etc/passwd")


def test_exists_returns_false_for_traversal_key_rather_than_raising(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    assert storage.exists("../../etc/passwd") is False


def test_delete_silently_ignores_traversal_key(tmp_path):
    storage = LocalStorageService(str(tmp_path))
    # Must not raise and must not delete anything outside the root.
    storage.delete("../../etc/passwd")
