# BottaBotAI

RAG 기반 문서 파싱·임베딩·채팅을 담당하는 AI 서버입니다.  
Java Spring 백엔드(`BottaBotBackend`)와 pgvector DB를 연동해 동작합니다.

## 구성

| 서비스 | 컨테이너 | 역할 |
|---|---|---|
| `api` | `bottabot_api` | FastAPI (문서 업로드, RAG 채팅 SSE) |
| `docling_worker` | `worker_docling` | Celery 워커 — Docling 파싱, 청킹, 임베딩 저장 |
| `ollama` | `ai_ollama` | 임베딩·채팅·요약 LLM |

외부 의존성(compose 밖):

- **PostgreSQL + pgvector** — 문서/채팅/벡터 저장
- **Redis** — Celery broker/backend

## 사전 준비

1. Docker / Docker Compose
2. 프로젝트 루트에 `.env` 파일
3. pgvector DB에 Java 백엔드와 동일한 스키마
4. Redis 기동 (`REDIS_URL`로 접근 가능해야 함)

### `.env` 예시

```env
# PostgreSQL (pgvector)
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=botta
DB_PASSWORD=your_password
DB_NAME=bottadb

# Redis
REDIS_URL=redis://host.docker.internal:6379/0

# Ollama (compose 내부에서는 서비스명 사용)
OLLAMA_URL=http://ollama:11434
OLLAMA_MODELS="embeddinggemma:latest gemma4:latest gemma3:1b"

# Hugging Face 리랭커 (API/워커 런타임에 캐시 다운로드)
HF_HOME=/opt/huggingface
HF_RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
```

Ollama 컨테이너는 기동 시 `.env`의 `OLLAMA_MODELS`(공백 구분)만 pull 하고, 목록에 없는 설치 모델은 삭제합니다.  
앱 런타임이 실제로 호출하는 모델은 `OLLAMA_EMBEDDING_MODEL`, `OLLAMA_CHAT_LLM`, `OLLAMA_SUMMARY_LLM`이며, 이 값들은 `OLLAMA_MODELS`에도 포함되어 있어야 합니다.  
Hugging Face 경로 형식(`org/name`)은 Ollama pull 대상이 아니며, `VectorStore` 초기화 시 캐시에 없으면 다운로드합니다.

## Docker Compose로 실행

```bash
# 이미지 빌드 + 기동
docker compose up -d --build

# 로그 확인 (첫 기동 시 Ollama 모델 pull에 시간이 걸릴 수 있음)
docker compose logs -f ollama
docker compose logs -f api
docker compose logs -f docling_worker

# 상태 확인
docker compose ps

# 종료
docker compose down
```

API는 Ollama가 healthy 상태가 된 뒤에 기동됩니다.

| 항목 | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| Ollama | http://localhost:11434 |

코드는 `./bottabot`이 컨테이너에 마운트되므로, API/워커 Python 코드 수정 후 프로세스 재시작만으로 반영되는 경우가 많습니다.

```bash
docker compose restart api docling_worker
```

의존성(`requirements/`) 변경 시에는 다시 빌드하세요.

```bash
docker compose build api docling_worker && docker compose up -d
```

## 서비스 흐름

### 문서 업로드 (인덱싱)

1. `POST /docparse/upload` → Celery `docling_parse` 태스크 enqueue  
2. Docling으로 마크다운 추출  
3. 청킹 → Ollama 임베딩 → `document` 저장 (`source_id` FK)  
4. `source` 상태: `PENDING` → `DONE` / `FAILED`

스키마를 `document.notebook_id`에서 `document.source_id`로 바꾼 경우 `scripts/migrate_document_fk_to_source.sql`을 먼저 실행한다.

### 채팅 (RAG)

1. `chat_session` → `notebook_id` 확인  
2. 노트북의 `source_id` 목록으로 청크를 하이브리드 검색(벡터 `$in` + BM25) + 리랭크  
3. 최근 대화 요약 (`OLLAMA_SUMMARY_LLM`)  
4. 프롬프트 구성 후 `OLLAMA_CHAT_LLM` SSE 스트리밍  
5. `chat` / `search_map` / `answer_detail` 저장

## API 엔드포인트

### `GET /`

헬스 체크.

```bash
curl http://localhost:8000/
```

응답 예:

```json
{"message": "BottaAI Server is working."}
```

---

### `POST /docparse/upload`

문서를 업로드하고 비동기 파싱·임베딩 태스크를 시작합니다.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `file` | file | O | PDF / DOCX / PPTX / XLSX |
| `notebook_id` | form UUID | O | 문서를 넣을 노트북 ID (DB에 존재해야 함) |

```bash
curl -X POST "http://localhost:8000/docparse/upload" \
  -F "notebook_id=22222222-2222-2222-2222-222222222222" \
  -F "file=@./sample.pdf"
```

응답 예:

```json
{"task_id": "celery-task-uuid"}
```

태스크 결과는 Celery/Redis에서 조회하며, 성공 시 pgvector `source` / `file` / `document`에 데이터가 쌓입니다.

---

### `POST /chat`

RAG 채팅. 응답은 **SSE** (`text/event-stream`)입니다.  
Spring 백엔드가 구독해 프론트로 재전송하는 용도로 설계되었습니다.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `chat_session_id` | form UUID | O | 대화 세션 (DB에 존재해야 함) |
| `prompt` | form string | O | 사용자 질문 |
| `images` | file[] | X | 첨부 이미지, 최대 5장 |

```bash
# 스트리밍 확인 (-N: 버퍼링 비활성)
curl -N -X POST "http://localhost:8000/chat" \
  -F "chat_session_id=3dbca2b6-5117-4156-938d-17562f40fbb2" \
  -F "prompt=이 노트북 문서 내용을 요약해줘"
```

이미지 포함:

```bash
curl -N -X POST "http://localhost:8000/chat" \
  -F "chat_session_id=<uuid>" \
  -F "prompt=이 이미지를 설명해줘" \
  -F "images=@./a.png" \
  -F "images=@./b.png"
```

SSE 이벤트 예:

```text
data: {"token":"Gunicorn"}

data: {"token":"은"}

data: [DONE]
```

오류 시:

```text
data: {"error":"..."}

data: [DONE]
```

Swagger(`/docs`)에서도 호출은 가능하지만, SSE 스트리밍 체감에는 `curl -N`이 적합합니다.

## 디렉터리 구조

```text
BottaBotAI/
├── bottabot/
│   ├── api/           # FastAPI 라우터
│   ├── celery/        # Celery 앱·태스크
│   ├── db/            # SQLAlchemy 모델·세션
│   ├── utils/         # DocParse, VectorStore, ChatService
│   ├── config.py
│   └── main.py
├── dockerfiles/
├── requirements/
│   ├── base.txt
│   ├── docling.txt
│   └── torch.txt
├── scripts/
│   └── ollama_entrypoint.sh
├── docker-compose.yml
└── .env
```

## 문제 해결

| 증상 | 확인 |
|---|---|
| `ai_ollama is unhealthy` | `docker compose logs ollama` — 모델 pull 실패/지연, `.env`의 `OLLAMA_MODELS` |
| `Qwen/... file does not exist` (Ollama) | HF 리랭커 이름을 `OLLAMA_MODELS`에 넣지 말 것. `HF_RERANKER_MODEL`만 사용 |
| API import / 패키지 오류 | `requirements` 변경 후 `docker compose build` |
| DB UUID / FK 오류 | `notebook`, `chat_session`이 존재하는지, ORM과 스키마가 맞는지 |
| orphan container 경고 | `docker compose up -d --remove-orphans` |
