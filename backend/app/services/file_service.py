"""파일 처리 서비스"""
import os
import hashlib
import logging
import aiofiles
import magic
from pathlib import Path
from typing import Optional, BinaryIO
from fastapi import UploadFile
from app.core.config import settings

logger = logging.getLogger(__name__)


class FileService:
    """파일 처리 서비스"""
    
    ALLOWED_MIME_TYPES = [
        'application/pdf',
        'application/x-pdf',
    ]
    
    CHUNK_SIZE = 1024 * 1024  # 1MB
    
    @staticmethod
    async def calculate_file_hash(file_path: str, algorithm: str = 'sha256') -> str:
        """파일 해시 계산 (스트리밍)"""
        hash_obj = hashlib.new(algorithm)
        
        async with aiofiles.open(file_path, 'rb') as f:
            while True:
                chunk = await f.read(FileService.CHUNK_SIZE)
                if not chunk:
                    break
                hash_obj.update(chunk)
        
        return hash_obj.hexdigest()
    
    @staticmethod
    def validate_pdf(file_path: str) -> bool:
        """PDF 파일 유효성 검사"""
        try:
            # MIME 타입 검사
            mime = magic.Magic(mime=True)
            file_mime = mime.from_file(file_path)
            
            if file_mime not in FileService.ALLOWED_MIME_TYPES:
                logger.warning(f"잘못된 MIME 타입: {file_mime}")
                return False
            
            # PDF 매직 넘버 검사 (%PDF)
            with open(file_path, 'rb') as f:
                header = f.read(5)
                if not header.startswith(b'%PDF-'):
                    logger.warning("PDF 매직 넘버가 없습니다")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"PDF 검증 실패: {e}")
            return False
    
    @staticmethod
    async def save_upload_file(
        upload_file: UploadFile, 
        destination: str,
        max_size: Optional[int] = None
    ) -> int:
        """업로드 파일을 스트리밍으로 저장"""
        max_size = max_size or settings.max_upload_size_bytes
        total_size = 0
        
        try:
            # 디렉토리 생성
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            
            async with aiofiles.open(destination, 'wb') as f:
                while True:
                    chunk = await upload_file.read(FileService.CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    total_size += len(chunk)
                    
                    # 크기 제한 확인
                    if total_size > max_size:
                        # 파일 삭제
                        await f.close()
                        os.remove(destination)
                        raise ValueError(f"파일 크기가 제한을 초과했습니다: {max_size} bytes")
                    
                    await f.write(chunk)
            
            logger.info(f"파일 저장 완료: {destination} ({total_size} bytes)")
            return total_size
            
        except Exception as e:
            logger.error(f"파일 저장 실패: {e}")
            # 실패 시 파일 삭제
            if os.path.exists(destination):
                os.remove(destination)
            raise
    
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
        """파일명 정리 (경로 조작 방지)"""
        # 디렉토리 구분자 제거
        filename = os.path.basename(filename)
        
        # 위험한 문자 제거
        dangerous_chars = ['..', '/', '\\', '\x00']
        for char in dangerous_chars:
            filename = filename.replace(char, '_')
        
        # 확장자 검증
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'
        
        return filename
    
    @staticmethod
    def scan_antivirus(file_path: str) -> bool:
        """안티바이러스 스캔 (ClamAV)"""
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
            else:
                logger.warning(f"바이러스 감지: {result}")
                return False
                
        except Exception as e:
            logger.error(f"안티바이러스 스캔 실패: {e}")
            # 스캔 실패 시 거부 (fail-secure)
            return False
    

















