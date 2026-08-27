"""PDF 압축 엔진 - 전략 패턴"""
import os
import logging
import subprocess
import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Dict, Any, List, Optional
import pikepdf
from app.models.job import CompressionPreset
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_pdf_info(pdf_path: str) -> Dict[str, Any]:
    """PDF 메타데이터 추출 (엔진과 무관하게 pikepdf로 읽는다)"""
    try:
        with pikepdf.open(pdf_path) as pdf:
            page_count = len(pdf.pages)

            image_count = 0
            for page in pdf.pages[:10]:  # 처음 10페이지만 샘플링
                if '/XObject' in page.Resources:
                    xobjects = page.Resources.XObject
                    for obj in xobjects:
                        if xobjects[obj].Subtype == '/Image':
                            image_count += 1

            if page_count > 10:
                image_count = int(image_count * (page_count / 10))

            # 비밀번호 없이 열렸으면 압축 가능한 파일로 처리.
            # Owner 비밀번호만 있는 권한 제한 PDF는 is_encrypted=True를 반환하지만
            # 실제로는 비밀번호 없이 열리므로 암호화된 것으로 취급하지 않는다.
            return {'page_count': page_count, 'image_count': image_count, 'encrypted': False}
    except pikepdf.PasswordError:
        # User 비밀번호가 필요한 진짜 암호화 PDF
        logger.warning(f"암호화된 PDF (비밀번호 필요): {pdf_path}")
        return {'page_count': 0, 'image_count': 0, 'encrypted': True}
    except Exception as e:
        logger.error(f"PDF 정보 추출 실패: {e}")
        return {'page_count': 0, 'image_count': 0, 'encrypted': False}


def _result(engine: str, input_path: str, output_path: str) -> Dict[str, Any]:
    """압축 결과 요약. 출력 파일이 없으면 실패로 본다."""
    if not os.path.exists(output_path):
        raise RuntimeError("출력 파일이 생성되지 않았습니다")

    input_size = os.path.getsize(input_path)
    output_size = os.path.getsize(output_path)
    logger.info(f"{engine} 압축 완료: {input_size} -> {output_size} bytes")

    return {
        'success': True,
        'engine': engine,
        'input_size': input_size,
        'output_size': output_size,
        'compression_ratio': output_size / input_size if input_size > 0 else 1.0,
    }


def _run_cli(cmd: List[str], engine: str) -> None:
    """외부 압축 CLI 실행. 타임아웃/실패를 RuntimeError로 정규화한다."""
    logger.info(f"{engine} 명령 실행: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, text=True,
                       timeout=settings.TASK_TIMEOUT_SECONDS, check=True)
    except subprocess.TimeoutExpired:
        logger.error(f"{engine} 타임아웃")
        raise RuntimeError(f"{engine} 작업 시간 초과")
    except subprocess.CalledProcessError as e:
        logger.error(f"{engine} 실패: {e.stderr}")
        raise RuntimeError(f"{engine} 압축 실패: {e.stderr}")


@lru_cache(maxsize=None)
def _which(binary: str) -> bool:
    """실행 파일 존재 여부. 프로세스 수명 동안 바뀌지 않으므로 캐시한다."""
    return shutil.which(binary) is not None


class CompressionEngine(ABC):
    """압축 엔진 추상 클래스"""

    @abstractmethod
    def compress(
        self,
        input_path: str,
        output_path: str,
        preset: CompressionPreset,
        options: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """PDF를 압축하고 결과 요약을 반환한다."""

    @abstractmethod
    def is_available(self) -> bool:
        """엔진 사용 가능 여부"""


class GhostscriptEngine(CompressionEngine):
    """Ghostscript 압축 엔진"""

    BINARY = 'gs' if os.name != 'nt' else 'gswin64c'

    PRESET_SETTINGS = {
        CompressionPreset.SCREEN: {'pdfsettings': '/screen', 'dpi': 72, 'jpeg_quality': 30},
        CompressionPreset.EBOOK: {'pdfsettings': '/ebook', 'dpi': 150, 'jpeg_quality': 60},
        CompressionPreset.PRINTER: {'pdfsettings': '/printer', 'dpi': 300, 'jpeg_quality': 80},
        CompressionPreset.PREPRESS: {'pdfsettings': '/prepress', 'dpi': 300, 'jpeg_quality': 90},
    }

    def is_available(self) -> bool:
        return _which(self.BINARY)

    def compress(self, input_path, output_path, preset, options=None, progress_callback=None):
        options = options or {}
        cfg = self.PRESET_SETTINGS.get(preset, self.PRESET_SETTINGS[CompressionPreset.EBOOK])
        dpi = cfg['dpi']

        cmd = [
            self.BINARY,
            '-sDEVICE=pdfwrite',
            '-dCompatibilityLevel=1.5',
            f"-dPDFSETTINGS={cfg['pdfsettings']}",
            '-dNOPAUSE',
            '-dQUIET',
            '-dBATCH',
            '-dDownsampleColorImages=true',
            f'-dColorImageResolution={dpi}',
            '-dDownsampleGrayImages=true',
            f'-dGrayImageResolution={dpi}',
            '-dDownsampleMonoImages=true',
            f'-dMonoImageResolution={dpi}',
            f"-dJPEGQ={cfg['jpeg_quality']}",
            '-dDetectDuplicateImages=true',
            '-dCompressFonts=true',
            '-dSubsetFonts=true',
            '-dCompressPages=true',
            f'-sOutputFile={output_path}',
            input_path,
        ]

        if progress_callback:
            progress_callback(0.3)
        _run_cli(cmd, 'ghostscript')
        if progress_callback:
            progress_callback(0.9)

        return _result('ghostscript', input_path, output_path)


class QPDFEngine(CompressionEngine):
    """qpdf 최적화 엔진"""

    def is_available(self) -> bool:
        return _which('qpdf')

    def compress(self, input_path, output_path, preset, options=None, progress_callback=None):
        cmd = [
            'qpdf',
            '--optimize-images',
            '--compression-level=9',
            '--linearize',
            '--object-streams=generate',
            '--remove-unreferenced-resources=yes',
            input_path,
            output_path,
        ]

        if progress_callback:
            progress_callback(0.3)
        _run_cli(cmd, 'qpdf')
        if progress_callback:
            progress_callback(0.9)

        return _result('qpdf', input_path, output_path)


class PikePDFEngine(CompressionEngine):
    """pikepdf 기반 경량 압축 엔진"""

    def is_available(self) -> bool:
        """항상 사용 가능 (순수 Python 패키지)"""
        return True

    def compress(self, input_path, output_path, preset, options=None, progress_callback=None):
        options = options or {}

        try:
            if progress_callback:
                progress_callback(0.2)

            with pikepdf.open(input_path) as pdf:
                if progress_callback:
                    progress_callback(0.5)

                if not options.get('preserve_metadata', True):
                    pdf.docinfo.clear()

                pdf.save(
                    output_path,
                    compress_streams=True,
                    stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                )

            if progress_callback:
                progress_callback(0.9)

            return _result('pikepdf', input_path, output_path)

        except Exception as e:
            logger.error(f"pikepdf 압축 실패: {e}")
            raise RuntimeError(f"pikepdf 압축 실패: {e}")


_ENGINES = {
    'ghostscript': GhostscriptEngine(),
    'qpdf': QPDFEngine(),
    'pikepdf': PikePDFEngine(),
}


def get_engine(engine_name: str) -> CompressionEngine:
    """엔진 인스턴스 반환. 이름이 틀리면 ValueError, 설치가 안 됐으면 폴백한다."""
    engine = _ENGINES.get(engine_name.lower())
    if not engine:
        raise ValueError(f"알 수 없는 엔진: {engine_name}")

    if engine.is_available():
        return engine

    logger.warning(f"엔진 {engine_name}을 사용할 수 없습니다")
    if not settings.ENABLE_ENGINE_FALLBACK:
        raise RuntimeError(f"엔진 {engine_name}을 사용할 수 없습니다")

    for name, fallback in _ENGINES.items():
        if fallback.is_available():
            logger.info(f"폴백 엔진 사용: {name}")
            return fallback

    # pikepdf는 항상 사용 가능하므로 여기 도달할 수 없다
    raise RuntimeError("사용 가능한 압축 엔진이 없습니다")
