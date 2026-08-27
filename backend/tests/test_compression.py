"""압축 엔진 테스트"""
import os
import pytest
from app.services.compression_engine import (
    GhostscriptEngine,
    QPDFEngine,
    PikePDFEngine,
    get_engine,
    get_pdf_info,
)
from app.models.job import CompressionPreset

ENGINES = [GhostscriptEngine(), QPDFEngine(), PikePDFEngine()]


@pytest.fixture
def written_pdf(sample_pdf_bytes, tmp_path):
    """샘플 PDF를 파일로 떨어뜨리고 (입력경로, 출력경로)를 준다"""
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(sample_pdf_bytes)
    return str(input_path), str(tmp_path / "output.pdf")


def test_pikepdf_engine_available():
    """PikePDF 엔진은 순수 Python이라 항상 사용 가능"""
    assert PikePDFEngine().is_available() is True


def test_get_engine_pikepdf():
    """엔진 가져오기 - PikePDF"""
    assert isinstance(get_engine('pikepdf'), PikePDFEngine)


def test_get_engine_unknown_name_raises():
    """이름이 틀린 엔진은 폴백이 아니라 ValueError"""
    with pytest.raises(ValueError):
        get_engine('nonexistent-engine')


def test_get_engine_falls_back_when_unavailable(monkeypatch):
    """설치되지 않은 엔진을 요청하면 사용 가능한 엔진으로 폴백한다"""
    monkeypatch.setattr(GhostscriptEngine, 'is_available', lambda self: False)
    monkeypatch.setattr(QPDFEngine, 'is_available', lambda self: False)

    engine = get_engine('ghostscript')
    assert isinstance(engine, PikePDFEngine)
    assert engine.is_available()


@pytest.mark.parametrize('engine', ENGINES, ids=lambda e: type(e).__name__)
def test_engine_compression(engine, written_pdf):
    """각 엔진이 실제로 압축 결과를 만든다 (설치된 엔진만)"""
    if not engine.is_available():
        pytest.skip(f"{type(engine).__name__} not available")

    input_path, output_path = written_pdf
    result = engine.compress(
        input_path=input_path,
        output_path=output_path,
        preset=CompressionPreset.EBOOK,
    )

    assert result['success'] is True
    assert os.path.exists(output_path)
    assert result['output_size'] > 0
    # 압축률은 입력에 따라 1을 넘을 수도 있다(작은 텍스트 PDF를 gs가 키우는 경우) — 값이 있는지만 본다
    assert result['compression_ratio'] > 0


def test_pdf_info_extraction(written_pdf):
    """PDF 정보 추출 테스트"""
    input_path, _ = written_pdf
    info = get_pdf_info(input_path)

    assert info['page_count'] > 0
    assert 'image_count' in info
    assert info['encrypted'] is False


def test_compression_presets():
    """압축 프리셋 값 확인"""
    assert {p.value for p in CompressionPreset} == {'screen', 'ebook', 'printer', 'prepress'}


def test_pikepdf_honors_preserve_metadata(written_pdf):
    """preserve_metadata=False면 pikepdf가 실제로 docinfo를 비운다 (라운드 1에서 고친 경로)"""
    import pikepdf

    input_path, output_path = written_pdf
    with pikepdf.open(input_path, allow_overwriting_input=True) as pdf:
        pdf.docinfo['/Title'] = 'secret title'
        # open_metadata는 종료 시 XMP를 docinfo로 동기화하므로 같은 값을 쓴다
        with pdf.open_metadata() as meta:
            meta['dc:title'] = 'secret title'
        pdf.save(input_path)

    engine = PikePDFEngine()

    engine.compress(input_path, output_path, CompressionPreset.EBOOK, preserve_metadata=True)
    with pikepdf.open(output_path) as pdf:
        assert str(pdf.docinfo.get('/Title')) == 'secret title'

    engine.compress(input_path, output_path, CompressionPreset.EBOOK, preserve_metadata=False)
    with pikepdf.open(output_path) as pdf:
        assert '/Title' not in pdf.docinfo
        assert pdf.open_metadata().get('dc:title') is None
