from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379/0"
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_EMBEDDING_MODEL: str = "embeddinggemma:latest"
    OLLAMA_CHAT_LLM: str = "gemma3:1b"
    OLLAMA_SUMMARY_LLM: str = "gemma3:1b"

    HF_HOME: str = "/opt/huggingface"
    HF_RERANKER_MODEL: str = "Qwen/Qwen3-Reranker-0.6B"

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DB_NAME: str = "postgres"

    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 128
    RETRIEVER_SEARCH_K: int = 20
    RERANKER_TOP_N: int = 5
    SK_WEIGHTS: list[float] = [0.7, 0.3]

    RAG_TOP_K: int = 5
    CHAT_HISTORY_LIMIT: int = 20
    RAG_SYSTEM_INSTRUCTIONS: str = (
        "당신은 제공된 자료에 근거해 답변하는 RAG 어시스턴트입니다.\n"
        "규칙:\n"
        "1. 검색된 문서 컨텍스트와 첨부 이미지를 우선 근거로 사용하세요.\n"
        "2. 컨텍스트에 없는 내용은 추측하지 말고, 모른다면 모른다고 말하세요.\n"
        "3. 답변은 명확하고 간결하게 작성하세요.\n"
        "4. 가능하면 어떤 근거를 참고했는지 짧게 언급하세요.\n"
        "5. 이전 대화 요약이 있으면 맥락을 유지하되, 현재 질문을 최우선으로 하세요.\n"
        "6. 이미지가 첨부된 경우 이미지 내용도 함께 고려하세요.\n"
        "7. 안전하지 않거나 해로운 요청에는 정중히 거절하세요."
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:

        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)
        return (
            f"postgresql+psycopg2://{user}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
