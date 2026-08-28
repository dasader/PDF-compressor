# PDF Compressor 운영 명령
#
# 배포 갱신은 `make rebuild` 하나면 된다 (git pull → 이미지 재빌드 → 컨테이너 재생성).
# 나머지는 그 과정을 나눠 쓰거나 결과를 확인하기 위한 것이다.

COMPOSE ?= docker compose
ENTRY   ?= http://127.0.0.1:8106

.DEFAULT_GOAL := help
.PHONY: help pull recreate rebuild logs ps down

help:  ## 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

pull:  ## 최신 코드 받기 (git pull)
	git pull --ff-only

recreate:  ## 이미지 재빌드 후 컨테이너 재생성
	$(COMPOSE) up -d --build --force-recreate --remove-orphans
	@$(MAKE) --no-print-directory ps

rebuild: pull recreate  ## pull + recreate (배포 갱신)

logs:  ## 전체 로그 따라가기 (Ctrl+C로 종료)
	$(COMPOSE) logs -f

ps:  ## 컨테이너 상태와 공개 포트
	@$(COMPOSE) ps
	@echo "진입점: $(ENTRY)  (이 머신 전용 · 다른 컨테이너는 pdf-network의 http://nginx:80)"

down:  ## 컨테이너 중지 및 제거 (데이터 볼륨은 유지)
	$(COMPOSE) down
