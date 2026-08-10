from fastapi import APIRouter, HTTPException, status, File, Form, UploadFile
from pydantic import BaseModel
import base64
import uuid

from bottabot.celery.tasks.docling_tasks import docling_parse

# 라우팅 설정
docparse_api_router = APIRouter(prefix="/docparse")


# 문서 분석 요청 응답 형식
class TaskResponse(BaseModel):
    task_id: str


@docparse_api_router.post("/upload", response_model=TaskResponse)
async def upload(
    file: UploadFile = File(...),
    notebook_id: uuid.UUID = Form(...),
):
    try:
        contents = await file.read()
        encoded = base64.b64encode(contents).decode("utf-8")
        task = docling_parse.delay(encoded, file.filename, str(notebook_id))
        return TaskResponse(task_id=task.id)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"docling_parse 태스크 호출 실패: {e}",
        )
