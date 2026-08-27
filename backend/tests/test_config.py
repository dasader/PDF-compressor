"""설정 파일 정합성 테스트"""
import pathlib

import pytest

from app.core.config import Settings

# 컨테이너 게이트는 backend/만 마운트하기도 해서 저장소 루트를 함께 찾는다
ENV_EXAMPLE = next(
    (p for p in (pathlib.Path(__file__).resolve().parents[2] / "env.example",
                 pathlib.Path("/repo/env.example"))
     if p.exists()),
    None,
)

pytestmark = pytest.mark.skipif(ENV_EXAMPLE is None, reason="env.example이 마운트 범위 밖")


def _env_keys() -> set:
    return {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def test_env_example_keys_match_settings():
    """env.example의 모든 키가 Settings에 존재해야 한다.

    pydantic-settings는 여분 키를 거부하므로, 어긋나면 문서화된
    `cp env.example .env` 절차가 앱 기동을 깨뜨린다.
    """
    unknown = _env_keys() - set(Settings.model_fields)
    assert not unknown, f"env.example에 Settings에 없는 키가 있다: {sorted(unknown)}"


def test_env_example_actually_loads(tmp_path):
    """env.example을 .env로 그대로 복사해도 Settings가 만들어져야 한다"""
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_EXAMPLE.read_text())

    settings = Settings(_env_file=str(env_file))
    assert settings.MAX_FILES_PER_BATCH > 0
    assert settings.MAX_UPLOAD_SIZE_MB > 0
