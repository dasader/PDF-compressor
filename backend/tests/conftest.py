"""Pytest 설정"""
import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.database import Base, get_db
from app.models.job import Job, JobStatus

# 테스트 데이터베이스
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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


@pytest.fixture
def setup_test_dirs():
    """테스트 디렉토리 설정"""
    test_dirs = ['./test_data/uploads', './test_data/results', './test_data/temp']
    for dir_path in test_dirs:
        os.makedirs(dir_path, exist_ok=True)

    yield

    import shutil
    if os.path.exists('./test_data'):
        shutil.rmtree('./test_data')
