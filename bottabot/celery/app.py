from celery import Celery

from bottabot.config import settings

# Celery 앱 초기화
celery_app = Celery(
    "bottabot",
    # task들을 redis가 관리하도록 설정
    broker=settings.redis_url,
    backend=settings.redis_url,
    # task 실행 주제들
    include=[
        "bottabot.celery.tasks.reranker",
        "bottabot.celery.tasks.docling",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # task가 등록될 큐를 지정
    task_routes={
        "bottabot.celery.tasks.reranker.*": {"queue": "reranker_queue"},
        "bottabot.celery.tasks.docling.*": {"queue": "docling_queue"},
    },
)
