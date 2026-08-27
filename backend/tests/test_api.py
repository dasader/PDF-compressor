"""API 테스트"""
import io
import os
from fastapi import status
from app.models.job import Job, JobStatus


def _upload(client, files, **data):
    return client.post("/api/upload", files=files, data={'engine': 'pikepdf', **data})


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


def test_upload_single_pdf(client, sample_pdf):
    """단일 PDF 업로드 테스트"""
    response = _upload(client, {'files': ('test.pdf', sample_pdf, 'application/pdf')},
                       preset='ebook', preserve_metadata='true')

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["jobs"]) == 1
    assert body["failed"] == []
    # 업로드 응답이 Job을 그대로 담으므로 클라이언트가 재조회할 필요가 없다
    assert body["jobs"][0]["original_filename"] == "test.pdf"
    assert body["jobs"][0]["status"] == "queued"


def test_upload_multiple_pdfs(client, sample_pdf_bytes):
    """다중 PDF 업로드 테스트"""
    files = [
        ('files', ('test1.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf')),
        ('files', ('test2.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf')),
    ]

    response = _upload(client, files, preset='screen')

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["jobs"]) == 2


def test_upload_invalid_file(client):
    """전부 잘못된 파일이면 400"""
    response = _upload(client, {'files': ('test.txt', io.BytesIO(b"Not a PDF"), 'text/plain')})

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_upload_partial_failure_keeps_good_files(client, sample_pdf_bytes):
    """한 파일이 실패해도 나머지는 살아남고 클라이언트가 추적할 수 있어야 한다"""
    files = [
        ('files', ('good.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf')),
        ('files', ('bad.pdf', io.BytesIO(b"Not a PDF"), 'application/pdf')),
    ]

    response = _upload(client, files)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["original_filename"] == "good.pdf"
    assert [f["filename"] for f in body["failed"]] == ["bad.pdf"]


def test_upload_rejects_oversized_batch(client, sample_pdf_bytes):
    """배치 상한을 넘으면 400"""
    from app.core.config import settings
    files = [
        ('files', (f'f{i}.pdf', io.BytesIO(sample_pdf_bytes), 'application/pdf'))
        for i in range(settings.MAX_FILES_PER_BATCH + 1)
    ]
    assert _upload(client, files).status_code == status.HTTP_400_BAD_REQUEST


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


def test_cancel_job_sets_expiry(client, db, make_job):
    """취소된 작업도 정리 대상이 되도록 expires_at이 채워져야 한다"""
    job = make_job(id="cancel-test-job", status=JobStatus.RUNNING)

    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == status.HTTP_200_OK

    db.refresh(job)
    assert job.status == JobStatus.CANCELLED
    assert job.expires_at is not None


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


def test_download_missing_result_is_404(client, make_job):
    """완료 상태여도 결과 파일이 없으면 404"""
    job = make_job(id="no-result", status=JobStatus.COMPLETED, result_file="gone.pdf")

    assert client.get(f"/api/jobs/{job.id}/download").status_code == status.HTTP_404_NOT_FOUND


def test_batch_download_zips_results(client, make_job, sample_pdf_bytes):
    """배치 ZIP은 디스크에 만들어 내려주고, 응답 후 임시 파일이 남지 않아야 한다"""
    import zipfile
    from app.core.config import settings

    job = make_job(id="zip-job", status=JobStatus.COMPLETED, result_file="compressed_zip-job.pdf")
    with open(job.result_path, 'wb') as f:
        f.write(sample_pdf_bytes)

    response = client.post("/api/jobs/batch/download", json=[job.id])
    assert response.status_code == status.HTTP_200_OK

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == [job.download_name]
        assert zf.testzip() is None

    # BackgroundTask가 임시 ZIP을 지웠는지
    assert os.listdir(settings.TEMP_DIR) == []


def test_cors_headers(client):
    """CORS 헤더 테스트"""
    response = client.options("/api/healthz", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    assert response.status_code in [status.HTTP_200_OK, status.HTTP_405_METHOD_NOT_ALLOWED]


def test_download_completed_job_with_expiry(client, make_job, sample_pdf_bytes):
    """만료 시각이 있는 완료 작업의 다운로드 (naive/aware 혼용으로 500이 나던 경로)"""
    from app.models.job import utcnow
    from datetime import timedelta

    job = make_job(id="dl-job", status=JobStatus.COMPLETED,
                   result_file="compressed_dl-job.pdf",
                   expires_at=utcnow() + timedelta(hours=24))
    with open(job.result_path, 'wb') as f:
        f.write(sample_pdf_bytes)

    response = client.get(f"/api/jobs/{job.id}/download")
    assert response.status_code == status.HTTP_200_OK
    assert response.content.startswith(b"%PDF-")
    assert "attachment" in response.headers["content-disposition"]


def test_download_expired_job_is_410(client, make_job, sample_pdf_bytes):
    """만료된 작업은 410"""
    from app.models.job import utcnow
    from datetime import timedelta

    job = make_job(id="expired-job", status=JobStatus.COMPLETED,
                   result_file="compressed_expired-job.pdf",
                   expires_at=utcnow() - timedelta(hours=1))
    with open(job.result_path, 'wb') as f:
        f.write(sample_pdf_bytes)

    assert client.get(f"/api/jobs/{job.id}/download").status_code == status.HTTP_410_GONE


def test_timestamps_serialize_as_utc(client, make_job):
    """DB의 naive UTC가 클라이언트에는 UTC로 표시돼야 한다"""
    job = make_job()
    created = client.get(f"/api/jobs/{job.id}").json()["created_at"]
    assert created.endswith("Z") or "+00:00" in created, created
