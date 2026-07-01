from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
import ollama
from bottabot.config import settings

# 라우팅 설정
embed_api_router = APIRouter(prefix="/embed")

# 임베딩 요청 응답 형식
class EmbedResponse(BaseModel):
    prompt: str = Field(
        ...,
        description="프롬프트",
    )
    embedding: list[float] = Field(
        ...,
        description="임베딩 결과로 만들어진 벡터"
    )

# 임베딩 함수
@embed_api_router.get("/embed_text", response_model=EmbedResponse)
def embed_text(prompt: str) -> EmbedResponse:
    try:
        client = ollama.Client(host=settings.OLLAMA_URL)  # 도커 compose 내부에서는 서비스명 사용

        response = client.embeddings(
            model=settings.OLLAMA_EMBEDDING_MODEL,
            prompt=prompt
        )

        embedding: list[float] = response["embedding"]

        if len(embedding) > 0:
            print(f"임베딩 성공: {embedding[:5]}...")

        # 200 OK
        return {
            "prompt": prompt, 
            "embedding": embedding
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"임베딩 실패: {e}"
        )

