"""파일 서비스 테스트"""
import os
from app.services.file_service import FileService, delete_job_files


def test_validate_pdf(sample_pdf, setup_test_dirs):
    """PDF 유효성 검사 테스트"""
    valid_path = './test_data/uploads/valid.pdf'
    with open(valid_path, 'wb') as f:
        f.write(sample_pdf.read())

    assert FileService.validate_pdf(valid_path) is True

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


def test_sanitize_filename_leaves_no_separator():
    """어떤 입력이 와도 결과에 경로 구분자/NUL이 남지 않는다"""
    for raw in ["../../etc/passwd", "..\\..\\win\\cfg", "/etc/shadow", "a\x00b.pdf", ".."]:
        got = FileService.sanitize_filename(raw)
        assert '/' not in got and '\\' not in got and '\x00' not in got
        assert os.path.basename(got) == got


def test_delete_job_files_is_idempotent(make_job, setup_test_dirs, monkeypatch):
    """파일이 이미 없어도 예외 없이 통과한다"""
    from app.core.config import settings
    monkeypatch.setattr(settings, 'UPLOAD_DIR', './test_data/uploads')
    monkeypatch.setattr(settings, 'RESULT_DIR', './test_data/results')

    job = make_job(filename='gone.pdf', result_file='gone_result.pdf')
    delete_job_files(job)  # 파일이 없는 상태

    with open(job.upload_path, 'wb') as f:
        f.write(b'%PDF-1.4\n')
    with open(job.result_path, 'wb') as f:
        f.write(b'%PDF-1.4\n')

    delete_job_files(job)
    assert not os.path.exists(job.upload_path)
    assert not os.path.exists(job.result_path)
