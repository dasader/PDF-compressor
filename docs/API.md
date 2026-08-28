# PDF Compressor — 연동 API 규칙

다른 서비스·에이전트가 이 서비스를 호출할 때 지켜야 할 계약입니다.
이 문서만 읽고 연동할 수 있도록 실제 동작 기준으로 정리했습니다.

---

## 1. 어디로 부르나

진입점은 **이 머신에서만** 접근할 수 있습니다 (`127.0.0.1:8106` 바인딩).
호출자 위치에 따라 주소가 다릅니다. **이걸 틀리면 연결이 안 됩니다.**

| 호출자 | 베이스 URL |
|--------|-----------|
| 같은 머신의 **호스트 프로세스** | `http://127.0.0.1:8106` |
| 같은 머신의 **다른 컨테이너** | `http://nginx:80` |
| 다른 장비 (LAN `192.168.0.x`) | **접근 불가** |

컨테이너에서 `127.0.0.1:8106`은 **그 컨테이너 자신**을 가리켜 닿지 않습니다.
호출자 compose에 네트워크를 붙이세요.

```yaml
services:
  my-service:
    networks: [default, pdf-network]

networks:
  pdf-network:
    external: true
    name: pdf-network
```

**인증 없음.** API 키·토큰이 필요 없고, 보내서도 안 됩니다(무시됩니다).

---

## 2. 기본 사용법 — `POST /api/compress`

**PDF 하나를 보내고 압축된 PDF를 응답 본문으로 그대로 받습니다.** 대부분 이것만 쓰면 됩니다.

### 요청

`multipart/form-data`

| 필드 | 필수 | 기본값 | 설명 |
|------|:----:|--------|------|
| `file` | ✅ | — | 압축할 PDF 파일 |
| `preset` | ❌ | `ebook` | `screen` / `ebook` / `printer` / `prepress` |
| `engine` | ❌ | `ghostscript` | `ghostscript`(최대 압축·손실) / `pikepdf`(무손실) |
| `preserve_metadata` | ❌ | `true` | 제목·작성자 등 메타데이터 보존 |

**옵션은 전부 생략 가능합니다.** 생략하면 위 기본값으로 처리합니다.

### 응답

| 코드 | 본문 | 의미 |
|------|------|------|
| **200** | `application/pdf` (압축 결과) | 성공 |
| **202** | JSON | 제한 시간(기본 300초) 내 미완료 — 나중에 받아간다 |
| **400** | JSON `{"detail": ...}` | PDF가 아니거나 크기 초과 |
| **422** | JSON `{"detail": ...}` | 압축 실패 (사유 포함) |

**200 응답 헤더**

| 헤더 | 예시 | 설명 |
|------|------|------|
| `X-Job-Id` | `3b48a62c-…` | 작업 ID |
| `X-Original-Size` | `10656` | 원본 바이트 |
| `X-Compressed-Size` | `4870` | 압축 후 바이트 |
| `X-Compression-Ratio` | `0.4570` | 압축본/원본 비율 (작을수록 많이 줄어듦) |
| `Content-Disposition` | `attachment; filename*=UTF-8''보고서_compressed.pdf` | 파일명 |

**202 응답 본문**

```json
{
  "job_id": "3b48a62c-...",
  "status": "processing",
  "download_url": "/api/jobs/3b48a62c-.../download",
  "message": "300초 안에 끝나지 않았습니다. download_url로 나중에 받아가세요."
}
```

→ `download_url`을 주기적으로 GET 하다가 **200**이 오면 그게 압축된 PDF입니다.
아직 진행 중이면 **400**(작업이 완료되지 않았습니다)이 옵니다.

### 예시

```bash
# 기본 옵션
curl -fsS -X POST http://nginx:80/api/compress -F "file=@input.pdf" -o output.pdf

# 옵션 지정
curl -fsS -X POST http://nginx:80/api/compress \
  -F "file=@input.pdf" -F "preset=screen" -F "engine=pikepdf" -F "preserve_metadata=false" \
  -o output.pdf
```

```python
import requests

BASE = "http://nginx:80"          # 컨테이너에서 호출할 때
# BASE = "http://127.0.0.1:8106"  # 호스트 프로세스에서 호출할 때

with open("input.pdf", "rb") as f:
    r = requests.post(f"{BASE}/api/compress", files={"file": f}, timeout=600)

if r.status_code == 200:
    open("output.pdf", "wb").write(r.content)
    print(r.headers["X-Original-Size"], "→", r.headers["X-Compressed-Size"])
elif r.status_code == 202:
    job = r.json()
    # job["download_url"] 을 폴링해서 200이 오면 본문이 결과 PDF
else:
    raise RuntimeError(r.json()["detail"])
```

---

## 3. 제한

| 항목 | 값 | 초과하면 |
|------|----|---------|
| 파일당 크기 | **512 MB** | 400 |
| 요청 하나의 전체 본문 | **600 MB** (nginx) | 413 |
| 배치당 파일 수 (`/api/upload`) | **10개** | 400 |
| 결과 보관 기간 | **24시간** | 이후 410 / 404 |
| 동기 대기 상한 | **300초** | 202 + `job_id` |

- **암호화된 PDF는 거부합니다** (사용자 비밀번호가 걸린 파일). 권한 제한만 걸린 PDF는 처리됩니다.
- 압축 작업은 **워커 1개가 순차 처리**합니다. 동시에 많이 보내면 큐에서 대기합니다.

---

## 4. 알아두면 좋은 동작

**중복 처리는 자동입니다.** 같은 파일(SHA-256 동일) + 같은 옵션이 24시간 안에 이미 처리됐다면,
다시 압축하지 않고 즉시 결과를 돌려줍니다. 같은 문서를 반복해서 보내도 비용이 들지 않으니
호출 측에서 따로 캐시를 만들 필요가 없습니다.

**결과 파일명 규칙**: `<원본이름>_compressed.pdf` (확장자 앞에 붙습니다).

**엔진 선택 기준** — 실측 결과입니다.

| 문서 유형 | `ghostscript` | `pikepdf` |
|-----------|--------------:|----------:|
| 스캔본 (이미지 위주) | **−97%** | 0% |
| 텍스트 문서 | −32% | **−41%** |

- **스캔 문서라면 `ghostscript`** 를 쓰세요. 유일하게 줄어듭니다. 대신 이미지 해상도를 절반으로 낮춥니다(손실).
- **원본 화질을 지켜야 하면 `pikepdf`.** 무손실이고 텍스트 문서에서 오히려 더 잘 줄지만, 스캔본은 거의 줄지 않습니다.
- 아주 작은 PDF는 `ghostscript`가 오히려 키울 수 있습니다(구조 오버헤드). `X-Compression-Ratio > 1`이면 원본을 쓰는 편이 낫습니다.

**텍스트는 보존됩니다.** 두 엔진 모두 텍스트를 이미지로 굽지 않으며, 스캔본에 얹힌
OCR 텍스트 레이어도 그대로 유지됩니다. 압축 후에도 검색·복사가 됩니다.

**`qpdf` 엔진은 제거됐습니다.** 옛 이름으로 요청해도 오류 없이 `pikepdf`로 처리합니다.

---

## 5. 여러 파일을 다룰 때 — 비동기 경로

한 번에 여러 파일을 보내거나 진행률을 추적해야 하면 이쪽을 씁니다.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/upload` | 최대 10개 업로드. 생성된 작업 목록을 바로 반환 |
| `GET` | `/api/jobs/{id}` | 작업 상태 조회 |
| `GET` | `/api/jobs/{id}/stream` | 상태 변화 SSE 구독 |
| `GET` | `/api/jobs/{id}/download` | 결과 PDF 다운로드 |
| `POST` | `/api/jobs/batch/download` | 여러 결과를 ZIP으로 (`["job_id", ...]` JSON 배열) |
| `POST` | `/api/jobs/{id}/cancel` | 진행 중 작업 취소 |
| `DELETE` | `/api/jobs/{id}` | 작업과 파일 삭제 |

`POST /api/upload` 응답:

```json
{
  "jobs": [ { "id": "...", "status": "queued", "original_size": 10656, "...": "..." } ],
  "failed": [ { "filename": "bad.pdf", "error": "유효하지 않은 PDF 파일입니다" } ],
  "message": "2개 파일 업로드 완료, 1개 실패"
}
```

**파일 하나가 실패해도 나머지는 처리됩니다.** 실패분은 `failed`에 담기며,
**전부 실패한 경우에만** 400이 납니다. 응답의 `jobs`에 Job 정보가 그대로 들어 있으니
파일마다 다시 조회할 필요가 없습니다.

`status` 값: `queued` → `running` → `completed` / `failed` / `cancelled`
(뒤의 셋이 종료 상태입니다).

### SSE 구독

```
GET /api/jobs/{id}/stream

event: snapshot
data: {"job_id":"...","status":"running","progress":0.3}

event: update
data: {"job_id":"...","type":"progress","progress":0.9}

event: update
data: {"job_id":"...","type":"status","status":"completed","compressed_size":4870,...}
```

접속 즉시 `snapshot`이 한 번 오고, 이후 변화가 `update`로 옵니다.
종료 상태가 오면 서버가 스트림을 닫습니다.

---

## 6. 상태 확인

| 경로 | 용도 |
|------|------|
| `GET /api/healthz` | 상태 + Redis 연결 여부 (`healthy` / `degraded`) |
| `GET /api/readyz` | 준비 여부. 준비 안 됐으면 **503** |
| `GET /docs` | Swagger UI (전체 스펙) |

---

## 7. 호출 측 권장 사항

1. **타임아웃을 넉넉히** 두세요. 동기 API는 최대 300초까지 기다립니다 (클라이언트 타임아웃은 그보다 길게).
2. **202를 반드시 처리하세요.** 큰 파일이나 큐가 밀린 상황에서 옵니다. 200만 가정하면 깨집니다.
3. **결과는 24시간 안에 받아가세요.** 이후에는 410(만료) 또는 404입니다.
4. **재시도는 그냥 다시 보내면 됩니다.** 중복 감지가 있어 같은 파일+옵션은 다시 압축하지 않습니다.
5. **`X-Compression-Ratio`를 확인하세요.** 1보다 크면 압축이 원본보다 커진 경우라 원본을 쓰는 게 낫습니다.
