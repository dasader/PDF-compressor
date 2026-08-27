"""중복 감지(dedup) 재사용 경로 테스트"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.api.upload import _reuse_completed_result
from app.models.job import Job, JobStatus


@pytest.fixture
def completed_source(db, make_job, sample_pdf_bytes):
    """결과 파일까지 갖춘 완료된 Job 하나"""
    job = make_job(
        id="source-job",
        filename="source-job.pdf",
        file_hash="deadbeef",
        status=JobStatus.COMPLETED,
        result_file="compressed_source-job.pdf",
        compressed_size=123,
        compression_ratio=0.5,
        page_count=10,
        image_count=0,
        preserve_metadata=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    with open(job.result_path, 'wb') as f:
        f.write(sample_pdf_bytes)
    return job


def _base(**overrides):
    return dict({
        'id': 'new-job',
        'user_session': None,
        'filename': 'new-job.pdf',
        'original_filename': 'again.pdf',
        'file_hash': 'deadbeef',
        'original_size': 999,
        'created_at': datetime.now(timezone.utc),
        'preset': 'ebook',
        'engine': 'ghostscript',
        'preserve_metadata': True,
    }, **overrides)


def test_reuses_existing_result(db, completed_source):
    """같은 해시+옵션이면 새 Job이 즉시 완료 상태로 만들어진다"""
    job = _reuse_completed_result(db, _base())

    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 1.0
    assert job.compressed_size == completed_source.compressed_size
    assert job.page_count == completed_source.page_count


def test_reused_result_is_its_own_file(db, completed_source):
    """경로를 공유하지 않고 하드링크를 만들어, 원본이 정리돼도 결과가 살아남는다"""
    job = _reuse_completed_result(db, _base())

    assert job.result_path != completed_source.result_path
    assert os.path.samefile(job.result_path, completed_source.result_path)

    # 원본 Job의 결과가 정리되어도
    os.remove(completed_source.result_path)
    assert job.result_exists
    assert open(job.result_path, 'rb').read().startswith(b'%PDF-')


def test_different_options_do_not_reuse(db, completed_source):
    """옵션이 다르면 재사용하지 않는다"""
    assert _reuse_completed_result(db, _base(preset='screen')) is None
    assert _reuse_completed_result(db, _base(engine='qpdf')) is None
    assert _reuse_completed_result(db, _base(preserve_metadata=False)) is None


def test_expired_source_is_not_reused(db, completed_source):
    """만료된 결과는 재사용하지 않는다"""
    completed_source.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    assert _reuse_completed_result(db, _base()) is None


def test_missing_result_file_is_not_reused(db, completed_source):
    """DB에는 있지만 파일이 사라졌으면 재사용하지 않는다"""
    os.remove(completed_source.result_path)

    assert _reuse_completed_result(db, _base()) is None
    assert db.query(Job).filter(Job.id == 'new-job').first() is None
