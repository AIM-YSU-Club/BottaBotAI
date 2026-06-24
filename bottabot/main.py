# FastAPI 서버 구현부
from fastapi import FastAPI

from bottabot.api.router import api_router

app = FastAPI(title="BottaBotAI")
app.include_router(api_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
