"""동기 압축 API 테스트 (POST /api/compress)"""
import io
import zipfile

import pytest
from fastapi import status

from app.api import compress as compress_mod
from app.models.job import Job, JobStatus
from app.workers import tasks as tasks_mod


@pytest.fixture
def run_task_inline(db, monkeypatch):
    """워커 없이 태스크를 요청 안에서 바로 실행시킨다.

    실제 워커와의 연동은 E2E가 검증한다. 여기서는 엔드포인트의 대기·응답 계약을 본다.
    """
    from contextlib import contextmanager
    import app.api.upload as upload_mod

    @contextmanager
    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(tasks_mod, "db_session", session)
    monkeypatch.setattr(tasks_mod, "_publish_job_event", lambda *a, **k: None)
    monkeypatch.setattr(compress_mod, "db_session", session)
    monkeypatch.setattr(
        upload_mod.compress_pdf_task, "apply_async",
        lambda args, task_id=None, **k: tasks_mod.compress_pdf_task.run(args[0]))
    return db


def _post(client, data, filename="report.pdf", **options):
    return client.post(
        "/api/compress",
        files={"file": (filename, io.BytesIO(data), "application/pdf")},
        data=options,
    )


def test_returns_compressed_pdf(client, sample_pdf_bytes, run_task_inline):
    """PDF를 보내면 압축된 PDF가 본문으로 바로 돌아온다"""
    response = _post(client, sample_pdf_bytes, engine="pikepdf")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert int(response.headers["x-compressed-size"]) == len(response.content)
    assert int(response.headers["x-original-size"]) == len(sample_pdf_bytes)
    assert float(response.headers["x-compression-ratio"]) > 0


def test_options_are_optional(client, sample_pdf_bytes, run_task_inline):
    """옵션을 하나도 주지 않아도 기본값으로 처리된다"""
    response = client.post(
        "/api/compress",
        files={"file": ("no-options.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )

    assert response.status_code == status.HTTP_200_OK
    job = run_task_inline.query(Job).filter(Job.id == response.headers["x-job-id"]).one()
    assert job.preset.value == "ebook"
    assert job.engine == "ghostscript"
    assert job.preserve_metadata is True


def test_options_are_honored(client, sample_pdf_bytes, run_task_inline):
    """준 옵션은 그대로 반영된다"""
    response = _post(client, sample_pdf_bytes,
                     preset="screen", engine="pikepdf", preserve_metadata="false")

    assert response.status_code == status.HTTP_200_OK
    job = run_task_inline.query(Job).filter(Job.id == response.headers["x-job-id"]).one()
    assert job.preset.value == "screen"
    assert job.engine == "pikepdf"
    assert job.preserve_metadata is False


def test_download_filename_has_compressed_suffix(client, sample_pdf_bytes, run_task_inline):
    """파일명은 접두사가 아니라 확장자 앞 접미사로 표시된다"""
    response = _post(client, sample_pdf_bytes, filename="분기 보고서.pdf", engine="pikepdf")

    assert response.status_code == status.HTTP_200_OK
    disposition = response.headers["content-disposition"]
    assert "_compressed.pdf" in disposition
    assert "compressed_" not in disposition


def test_invalid_pdf_is_400(client, run_task_inline):
    """PDF가 아니면 400"""
    response = _post(client, b"not a pdf at all")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_failed_job_is_422(client, sample_pdf_bytes, monkeypatch, run_task_inline):
    """압축이 실패하면 422와 사유를 준다"""
    import app.api.upload as upload_mod

    def fail(args, task_id=None, **k):
        with tasks_mod.db_session() as db:
            job = db.query(Job).filter(Job.id == args[0]).first()
            job.status = JobStatus.FAILED
            job.error_message = "엔진이 터졌습니다"

    monkeypatch.setattr(upload_mod.compress_pdf_task, "apply_async", fail)

    response = _post(client, sample_pdf_bytes)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "엔진이 터졌습니다" in response.json()["detail"]


def test_timeout_returns_job_id(client, sample_pdf_bytes, monkeypatch, run_task_inline):
    """제한 시간 안에 안 끝나면 202와 job_id/download_url을 준다"""
    import app.api.upload as upload_mod
    from app.core.config import settings

    monkeypatch.setattr(upload_mod.compress_pdf_task, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(settings, "SYNC_COMPRESS_TIMEOUT_SECONDS", 0)

    response = _post(client, sample_pdf_bytes)

    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["job_id"]
    assert body["download_url"] == f"/api/jobs/{body['job_id']}/download"


def test_dedup_returns_existing_result_without_recompressing(
        client, sample_pdf_bytes, run_task_inline, monkeypatch):
    """같은 파일+옵션을 다시 보내면 압축 없이 즉시 결과를 준다"""
    first = _post(client, sample_pdf_bytes, engine="pikepdf")
    assert first.status_code == status.HTTP_200_OK

    import app.api.upload as upload_mod
    calls = []
    monkeypatch.setattr(upload_mod.compress_pdf_task, "apply_async",
                        lambda *a, **k: calls.append(a))

    second = _post(client, sample_pdf_bytes, engine="pikepdf")

    assert second.status_code == status.HTTP_200_OK
    assert second.content == first.content
    assert calls == [], "재사용했다면 압축 태스크를 디스패치하지 않아야 한다"


def test_batch_zip_uses_new_filename(client, make_job, sample_pdf_bytes):
    """배치 ZIP 안의 이름도 새 규칙을 따른다"""
    job = make_job(id="zip-name", original_filename="보고서.pdf",
                   status=JobStatus.COMPLETED, result_file="compressed_zip-name.pdf")
    with open(job.result_path, "wb") as f:
        f.write(sample_pdf_bytes)

    response = client.post("/api/jobs/batch/download", json=[job.id])

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == ["보고서_compressed.pdf"]
