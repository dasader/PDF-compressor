"""API 테스트"""
import io
from fastapi import status
from app.models.job import Job, JobStatus


def test_health_check(client):
    """헬스체크 테스트"""
    response = client.get("/api/healthz")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]
    assert "version" in data


def test_root_endpoint(client):
    """루트 엔드포인트 테스트"""
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "running"


def test_upload_single_pdf(client, sample_pdf, setup_test_dirs):
    """단일 PDF 업로드 테스트"""
    response = client.post(
        "/api/upload",
        files={'files': ('test.pdf', sample_pdf, 'application/pdf')},
        data={
            'preset': 'ebook',
            'engine': 'pikepdf',  # 항상 사용 가능한 엔진
            'preserve_metadata': 'true',
            'preserve_ocr': 'true',
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["job_ids"]) == 1


def test_upload_multiple_pdfs(client, sample_pdf_bytes, setup_test_dirs):
    """다중 PDF 업로드 테스트"""
    files = [
        ('files', ('test1.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf')),
        ('files', ('test2.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf')),
    ]

    response = client.post("/api/upload", files=files, data={'preset': 'screen', 'engine': 'pikepdf'})

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["job_ids"]) == 2


def test_upload_invalid_file(client):
    """잘못된 파일 업로드 테스트"""
    response = client.post(
        "/api/upload",
        files={'files': ('test.txt', io.BytesIO(b"Not a PDF"), 'text/plain')},
        data={'preset': 'ebook'},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_get_job_status(client, make_job):
    """작업 상태 조회 테스트"""
    job = make_job()

    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == job.id
    assert data["status"] == "queued"


def test_get_nonexistent_job(client):
    """존재하지 않는 작업 조회 테스트"""
    response = client.get("/api/jobs/nonexistent-id")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_cancel_job(client, db, make_job):
    """작업 취소 테스트"""
    job = make_job(id="cancel-test-job", status=JobStatus.RUNNING)

    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == status.HTTP_200_OK

    db.refresh(job)
    assert job.status == JobStatus.CANCELLED


def test_cancel_terminal_job_rejected(client, make_job):
    """이미 끝난 작업은 취소할 수 없다"""
    job = make_job(id="done-job", status=JobStatus.COMPLETED)

    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_delete_job(client, db, make_job):
    """작업 삭제 테스트"""
    job = make_job(id="delete-test-job", status=JobStatus.COMPLETED)

    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == status.HTTP_200_OK

    assert db.query(Job).filter(Job.id == job.id).first() is None


def test_list_jobs(client, make_job):
    """작업 목록 조회 테스트"""
    for i in range(5):
        make_job(
            id=f"list-test-job-{i}",
            filename=f"test{i}.pdf",
            original_filename=f"test{i}.pdf",
            status=JobStatus.QUEUED if i % 2 == 0 else JobStatus.COMPLETED,
        )

    response = client.get("/api/jobs")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 5

    response = client.get("/api/jobs?status=queued")
    assert response.status_code == status.HTTP_200_OK
    assert all(job["status"] == "queued" for job in response.json())


def test_cors_headers(client):
    """CORS 헤더 테스트"""
    response = client.options("/api/healthz", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    assert response.status_code in [status.HTTP_200_OK, status.HTTP_405_METHOD_NOT_ALLOWED]
