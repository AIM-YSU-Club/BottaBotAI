# 기본적인 라우터 예제
from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 만들어놓은 task 함수들 불러오기
from bottabot.celery.app import celery_app
from bottabot.celery.tasks.docling import hello_world as docling_hello_world
from bottabot.celery.tasks.reranker import hello_world as reranker_hello_world

# 라우팅 설정
api_router = APIRouter(prefix="/api")

# 응답 형식 정의
class TaskResponse(BaseModel):
    task_id: str

# API 함수 정의
@api_router.post("/rerank", response_model=TaskResponse)
def enqueue_rerank() -> TaskResponse:
    # API 호출 시 task 등록
    task = reranker_hello_world.delay()
    # task 등록 후 id를 응답으로 반환
    return TaskResponse(task_id=task.id)


@api_router.post("/docling", response_model=TaskResponse)
def enqueue_docling() -> TaskResponse:
    task = docling_hello_world.delay()
    return TaskResponse(task_id=task.id)

# task id로 task 상태 조회
@api_router.get("/tasks/{task_id}")
def get_task_status(task_id: str) -> dict:
    result = AsyncResult(task_id, app=celery_app)
    if result.state == "PENDING":
        return {"task_id": task_id, "status": "pending"}
    if result.state == "FAILURE":
        raise HTTPException(status_code=500, detail=str(result.result))
    return {
        "task_id": task_id,
        "status": result.state.lower(),
        "result": result.result,
    }
