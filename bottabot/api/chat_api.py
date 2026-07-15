from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from bottabot.utils.ChatService import ChatService

# Spring 백엔드가 구독할 RAG 채팅 SSE 엔드포인트
chat_api_router = APIRouter(prefix="/chat")


def _sse_event(payload: str) -> str:
    """SSE 한 이벤트 포맷: data: <payload>\\n\\n"""
    return f"data: {payload}\n\n"


def _stream_chat_events(
    service: ChatService,
    *,
    chat_session_id: uuid.UUID,
    prompt: str,
    images: list[bytes],
) -> Iterator[str]:
    """
    ChatService 토큰 스트림을 Spring 친화적 SSE로 변환한다.
    - 정상 토큰: data: {"token":"..."}
    - 종료: data: [DONE]
    - 오류도 연결을 유지한 채 error 이벤트로 전달 후 [DONE]
    """
    try:
        for token in service.stream_chat(
            chat_session_id=chat_session_id,
            prompt=prompt,
            images=images,
        ):
            yield _sse_event(json.dumps({"token": token}, ensure_ascii=False))
        yield _sse_event("[DONE]")
    except ValueError as e:
        yield _sse_event(json.dumps({"error": str(e)}, ensure_ascii=False))
        yield _sse_event("[DONE]")
    except Exception as e:
        yield _sse_event(
            json.dumps({"error": f"채팅 스트리밍 실패: {e}"}, ensure_ascii=False)
        )
        yield _sse_event("[DONE]")


@chat_api_router.post("")
async def chat(
    chat_session_id: uuid.UUID = Form(...),
    prompt: str = Form(...),
    images: list[UploadFile] | None = File(None),
):
    """
    multipart 채팅 요청.

    Form:
    - chat_session_id: 현재 대화 세션 (notebook 범위 검색에 사용)
    - prompt: 사용자 질문
    - images: 선택, 최대 5장 (Ollama multimodal로 전달)

    응답: text/event-stream
    부수 효과: chat / search_map / answer_detail 레코드 삽입·갱신
    """
    prompt_text = prompt.strip()
    if not prompt_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="prompt는 비어 있을 수 없습니다.",
        )

    uploaded = images or []
    if len(uploaded) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미지는 최대 5장까지 첨부할 수 있습니다.",
        )

    image_bytes: list[bytes] = []
    for image in uploaded:
        data = await image.read()
        if data:
            image_bytes.append(data)

    # 스트리밍 시작 전에 세션 존재 여부를 확인해 404를 바로 반환한다.
    service = ChatService()
    try:
        service.get_chat_session(chat_session_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return StreamingResponse(
        _stream_chat_events(
            service,
            chat_session_id=chat_session_id,
            prompt=prompt_text,
            images=image_bytes,
        ),
        media_type="text/event-stream",
        headers={
            # 프록시/브라우저가 청크를 버퍼링하지 않도록 힌트를 준다.
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
