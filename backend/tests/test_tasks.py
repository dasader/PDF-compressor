"""워커 태스크 테스트 — 라운드 2에서 가장 크게 바꾼 동작을 고정한다."""
import os

import pytest

from app.models.job import Job, JobStatus, utcnow
from app.workers import tasks as tasks_mod


@pytest.fixture
def worker_db(db, monkeypatch):
    """워커의 db_session()이 테스트 세션을 쓰도록 바꾼다."""
    from contextlib import contextmanager

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
    return db


def test_compress_task_completes_and_cleans_source(worker_db, make_job, sample_pdf_bytes):
    """성공하면 완료 상태·만료 시각이 기록되고 업로드 원본이 지워진다"""
    job = make_job(id="task-ok", filename="task-ok.pdf", engine="pikepdf")
    with open(job.upload_path, "wb") as f:
        f.write(sample_pdf_bytes)

    result = tasks_mod.compress_pdf_task.run("task-ok")

    assert result["success"] is True
    worker_db.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 1.0
    assert job.compressed_size > 0
    assert job.page_count > 0
    assert job.expires_at is not None
    assert job.result_exists
    # 결과가 확정되면 원본은 보관 기간 동안 들고 있을 이유가 없다
    assert not os.path.exists(job.upload_path)


def test_compress_task_missing_input_sets_expiry_after_retries(worker_db, make_job):
    """재시도 한도를 넘긴 실패도 만료 시각을 받아 정리 대상이 된다"""
    job = make_job(id="task-fail", filename="task-fail.pdf", engine="pikepdf",
                   retry_count=99)  # 한도 초과 상태로 시작

    with pytest.raises(FileNotFoundError):
        tasks_mod.compress_pdf_task.run("task-fail")

    worker_db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_message
    assert job.expires_at is not None
    assert job.result_file is None


def test_cleanup_removes_expired_jobs_and_files(worker_db, make_job, sample_pdf_bytes):
    """정리 작업이 만료된 작업의 행과 파일을 함께 지운다"""
    from datetime import timedelta

    expired = make_job(id="old", filename="old.pdf", result_file="compressed_old.pdf",
                       status=JobStatus.COMPLETED, expires_at=utcnow() - timedelta(hours=1))
    for path in (expired.upload_path, expired.result_path):
        with open(path, "wb") as f:
            f.write(sample_pdf_bytes)
    upload_path, result_path = expired.upload_path, expired.result_path

    alive = make_job(id="new", filename="new.pdf", status=JobStatus.COMPLETED,
                     expires_at=utcnow() + timedelta(hours=1))

    tasks_mod.cleanup_old_files_task.run()

    assert worker_db.query(Job).filter(Job.id == "old").first() is None
    assert not os.path.exists(upload_path)
    assert not os.path.exists(result_path)
    # 아직 만료되지 않은 작업은 건드리지 않는다
    assert worker_db.query(Job).filter(Job.id == alive.id).first() is not None


def test_running_job_is_visible_before_compression_finishes(worker_db, make_job, sample_pdf_bytes):
    """압축 전에 RUNNING이 커밋되어야 SSE 스냅샷이 진행 중임을 알 수 있다"""
    job = make_job(id="task-running", filename="task-running.pdf", engine="pikepdf")
    with open(job.upload_path, "wb") as f:
        f.write(sample_pdf_bytes)

    seen = {}
    original = tasks_mod.get_pdf_info

    def spy(path):
        worker_db.refresh(job)
        seen["status"] = job.status
        seen["started_at"] = job.started_at
        return original(path)

    tasks_mod.get_pdf_info = spy
    try:
        tasks_mod.compress_pdf_task.run("task-running")
    finally:
        tasks_mod.get_pdf_info = original

    assert seen["status"] == JobStatus.RUNNING
    assert seen["started_at"] is not None
