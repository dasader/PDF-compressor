"""Pytest 설정"""
import os
import tempfile

# app.models.database가 import 시점에 DB_PATH를 읽으므로 그 전에 지정해야 한다.
# 이렇게 해야 컨테이너 밖(/data 없음)에서도 테스트가 돌아간다.
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "pytest.db"))

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.main import app
from app.models.database import Base, get_db
from app.models.job import Job, JobStatus

# 테스트 데이터베이스
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """업로드/결과/임시 디렉터리를 테스트마다 격리한다 (컨테이너 밖에서도 돌아가도록)."""
    for name in ("UPLOAD_DIR", "RESULT_DIR", "TEMP_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(settings, name, str(d))
    return tmp_path


@pytest.fixture(scope="function")
def db():
    """테스트 데이터베이스 세션"""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    """테스트 클라이언트"""
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_job(db):
    """Job 레코드 팩토리 — 필요한 필드만 덮어쓴다."""
    def _make(**overrides) -> Job:
        job = Job(**{
            'id': 'test-job-id',
            'filename': 'test.pdf',
            'original_filename': 'test.pdf',
            'original_size': 1_000_000,
            'status': JobStatus.QUEUED,
            'preset': 'ebook',
            'engine': 'ghostscript',
            'created_at': datetime.now(timezone.utc),
            **overrides,
        })
        db.add(job)
        db.commit()
        return job
    return _make


@pytest.fixture
def sample_pdf_bytes():
    """샘플 PDF 바이트 (여러 번 읽어도 안전)"""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    import io

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    for i in range(10):
        c.drawString(100, 750, f"Test PDF - Page {i+1}")
        c.drawString(100, 700, "This is a test PDF file for compression testing.")
        c.drawString(100, 650, "Lorem ipsum dolor sit amet, consectetur adipiscing elit.")
        c.showPage()

    c.save()
    return buffer.getvalue()


@pytest.fixture
def sample_pdf(sample_pdf_bytes):
    """샘플 PDF 파일 스트림"""
    import io
    return io.BytesIO(sample_pdf_bytes)
