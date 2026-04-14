from sqlalchemy import text
from app.models.database import engine


def test_wal_mode_enabled():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert str(result).lower() == "wal"


def test_synchronous_normal():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA synchronous")).scalar()
    # NORMAL = 1
    assert int(result) == 1
