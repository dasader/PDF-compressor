"""파일 서비스 테스트"""
import os
import pytest
from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_calculate_file_hash(sample_pdf, setup_test_dirs):
    """파일 해시 계산 테스트"""
    file_path = './test_data/uploads/hash_test.pdf'
    
    with open(file_path, 'wb') as f:
        f.write(sample_pdf.read())
    
    hash1 = await FileService.calculate_file_hash(file_path, 'sha256')
    assert hash1 is not None
    assert len(hash1) == 64  # SHA256은 64자
    
    # 같은 파일은 같은 해시
    hash2 = await FileService.calculate_file_hash(file_path, 'sha256')
    assert hash1 == hash2


def test_validate_pdf(sample_pdf, setup_test_dirs):
    """PDF 유효성 검사 테스트"""
    # 유효한 PDF
    valid_path = './test_data/uploads/valid.pdf'
    with open(valid_path, 'wb') as f:
        f.write(sample_pdf.read())
    
    assert FileService.validate_pdf(valid_path) is True
    
    # 잘못된 파일
    invalid_path = './test_data/uploads/invalid.pdf'
    with open(invalid_path, 'wb') as f:
        f.write(b"Not a PDF file")
    
    assert FileService.validate_pdf(invalid_path) is False


def test_sanitize_filename():
    """파일명 정리 테스트"""
    # 경로 조작 시도
    assert FileService.sanitize_filename("../../../etc/passwd") == "passwd.pdf"
    assert FileService.sanitize_filename("test..pdf") == "test..pdf"
    assert FileService.sanitize_filename("../../dangerous.pdf") == "dangerous.pdf"
    
    # 일반 파일명
    assert FileService.sanitize_filename("normal_file.pdf") == "normal_file.pdf"
    
    # 확장자 없는 경우
    assert FileService.sanitize_filename("noextension").endswith(".pdf")


@pytest.mark.asyncio
async def test_save_upload_file(sample_pdf, setup_test_dirs):
    """업로드 파일 저장 테스트"""
    from fastapi import UploadFile
    import io
    
    # 모의 UploadFile 생성
    file_content = sample_pdf.read()
    upload_file = UploadFile(
        filename="test.pdf",
        file=io.BytesIO(file_content)
    )
    
    destination = './test_data/uploads/saved.pdf'
    size = await FileService.save_upload_file(upload_file, destination)
    
    assert os.path.exists(destination)
    assert size == len(file_content)
    assert os.path.getsize(destination) == size


@pytest.mark.asyncio
async def test_save_upload_file_size_limit(sample_pdf, setup_test_dirs):
    """파일 크기 제한 테스트"""
    from fastapi import UploadFile
    import io
    
    file_content = sample_pdf.read()
    upload_file = UploadFile(
        filename="test.pdf",
        file=io.BytesIO(file_content)
    )
    
    destination = './test_data/uploads/too_large.pdf'
    
    # 매우 작은 크기 제한으로 테스트
    with pytest.raises(ValueError, match="파일 크기가 제한을 초과"):
        await FileService.save_upload_file(
            upload_file, 
            destination, 
            max_size=100  # 100 bytes
        )
    
    # 파일이 생성되지 않아야 함
    assert not os.path.exists(destination)


# test_cleanup_old_files 제거: FileService.cleanup_old_files 는 cleanup_old_files_task 로 통합됨


















