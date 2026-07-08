# FastAPI 서버 구현부
import asyncio
from fastapi import FastAPI, Request
# API 라우터
from bottabot.api.embed_api import embed_api_router 
from bottabot.api.docparse_api import docparse_api_router

app = FastAPI(title="BottaBotAI")
app.include_router(embed_api_router)
app.include_router(docparse_api_router)

@app.get("/")
async def read_root():
    return {"message": "BottaAI Server is working."}