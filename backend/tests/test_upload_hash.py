import hashlib
import io
import pytest
from app.services.file_service import FileService


class FakeUploadFile:
    """Fake UploadFile implementing only async .read(n) for these tests."""
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    async def read(self, n: int) -> bytes:
        return self._buf.read(n)


@pytest.mark.asyncio
async def test_save_and_hash_single_pass(tmp_path):
    data = b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024)  # 3MB
    dest = tmp_path / "out.pdf"

    size, digest = await FileService.save_upload_file_with_hash(
        FakeUploadFile(data), str(dest), max_size=10 * 1024 * 1024
    )

    assert size == len(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data


@pytest.mark.asyncio
async def test_save_and_hash_over_limit(tmp_path):
    data = b"x" * (2 * 1024 * 1024)
    dest = tmp_path / "out.pdf"
    with pytest.raises(ValueError):
        await FileService.save_upload_file_with_hash(
            FakeUploadFile(data), str(dest), max_size=1024 * 1024
        )
    assert not dest.exists()
