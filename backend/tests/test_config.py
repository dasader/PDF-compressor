"""env.example ↔ Settings 정합성 테스트.

env.example은 실행에 쓰이지 않는 참조표지만, Settings와 어긋나면
문서가 존재하지 않는 설정을 안내하게 되므로 여기서 막는다.
"""
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
    """env.example의 모든 키가 Settings에 존재해야 한다 (없는 설정을 안내하지 않도록)."""
    unknown = _env_keys() - set(Settings.model_fields)
    assert not unknown, f"env.example에 Settings에 없는 키가 있다: {sorted(unknown)}"


def test_env_example_actually_loads(tmp_path):
    """참조표의 값들이 Settings의 타입 검증을 실제로 통과해야 한다"""
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_EXAMPLE.read_text())

    settings = Settings(_env_file=str(env_file))
    assert settings.MAX_FILES_PER_BATCH > 0
    assert settings.MAX_UPLOAD_SIZE_MB > 0
