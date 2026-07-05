from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379/0"
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_EMBEDDING_MODEL: str = "embeddinggemma:latest"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()