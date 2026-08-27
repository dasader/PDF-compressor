"""파일 처리 서비스"""
import os
import hashlib
import logging
import aiofiles
import magic
from pathlib import Path
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

# libmagic DB는 로드가 비싸다 — 요청마다 새로 만들지 않고 모듈 수준에서 한 번만 연다
_MAGIC = magic.Magic(mime=True)


class FileService:
    """파일 처리 서비스"""

    ALLOWED_MIME_TYPES = [
        'application/pdf',
        'application/x-pdf',
    ]

    CHUNK_SIZE = 1024 * 1024  # 1MB

    @staticmethod
    def validate_pdf(file_path: str) -> bool:
        """PDF 파일 유효성 검사"""
        try:
            file_mime = _MAGIC.from_file(file_path)
            if file_mime not in FileService.ALLOWED_MIME_TYPES:
                logger.warning(f"잘못된 MIME 타입: {file_mime}")
                return False

            with open(file_path, 'rb') as f:
                if not f.read(5).startswith(b'%PDF-'):
                    logger.warning("PDF 매직 넘버가 없습니다")
                    return False

            return True

        except Exception as e:
            logger.error(f"PDF 검증 실패: {e}")
            return False

    @staticmethod
    async def save_upload_file_with_hash(
        upload_file,
        destination: str,
        max_size: Optional[int] = None,
    ) -> tuple:
        """저장과 SHA-256 해시를 단일 패스로 수행.

        Returns:
            (size_bytes, sha256_hex)
        """
        max_size = max_size or settings.max_upload_size_bytes
        total_size = 0
        hasher = hashlib.sha256()

        try:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(destination, 'wb') as f:
                while True:
                    chunk = await upload_file.read(FileService.CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > max_size:
                        await f.close()
                        if os.path.exists(destination):
                            os.remove(destination)
                        raise ValueError(f"파일 크기가 제한을 초과했습니다: {max_size} bytes")
                    hasher.update(chunk)
                    await f.write(chunk)
            logger.info(f"파일+해시 저장 완료: {destination} ({total_size} bytes)")
            return total_size, hasher.hexdigest()
        except Exception:
            if os.path.exists(destination):
                os.remove(destination)
            raise

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """파일명 정리 (경로 조작 방지).

        경로 상승(`..`)은 basename이 이미 제거하므로, 남은 구분자와 NUL만 무력화한다.
        """
        filename = os.path.basename(filename)

        for char in ('/', '\\', '\x00'):
            filename = filename.replace(char, '_')

        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'

        return filename

    @staticmethod
    def scan_antivirus(file_path: str) -> bool:
        """안티바이러스 스캔 (ClamAV). 비활성화면 통과."""
        if not settings.ENABLE_ANTIVIRUS:
            return True

        try:
            import clamd
            cd = clamd.ClamdNetworkSocket(
                host=settings.CLAMAV_HOST,
                port=settings.CLAMAV_PORT
            )

            result = cd.scan(file_path)

            if result is None:
                logger.info(f"바이러스 스캔 통과: {file_path}")
                return True

            logger.warning(f"바이러스 감지: {result}")
            return False

        except Exception as e:
            logger.error(f"안티바이러스 스캔 실패: {e}")
            # 스캔 실패 시 거부 (fail-secure)
            return False


def delete_job_files(job) -> None:
    """작업의 업로드/결과 파일을 삭제한다. 이미 없으면 조용히 넘어간다."""
    for path in (job.upload_path, job.result_path):
        if not path:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"파일 삭제 실패: {path}: {e}")
