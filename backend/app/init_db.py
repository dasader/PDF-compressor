"""데이터베이스 초기화 스크립트"""
import logging
from app.models.database import engine, Base
from app.models.job import Job  # 모델 import 필수 — create_all이 테이블을 인식하려면 필요하다

logger = logging.getLogger(__name__)


def init_db() -> None:
    """데이터베이스 테이블 생성. 실패하면 예외를 그대로 올려 기동을 중단시킨다."""
    Base.metadata.create_all(bind=engine)
    logger.info("데이터베이스 테이블 생성 완료")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
