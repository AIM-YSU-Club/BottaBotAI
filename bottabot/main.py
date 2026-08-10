# FastAPI 서버 구현부
from fastapi import FastAPI

from bottabot.api.chat_api import chat_api_router
from bottabot.api.docparse_api import docparse_api_router

app = FastAPI(title="BottaBotAI")
app.include_router(docparse_api_router)
app.include_router(chat_api_router)


@app.get("/")
async def read_root():
    return {"message": "BottaAI Server is working."}
