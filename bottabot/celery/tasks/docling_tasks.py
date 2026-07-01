from bottabot.celery.app import celery_app
import ollama
from bottabot.config import settings

# task 함수 예시
@celery_app.task(
    name="bottabot.celery.tasks.docling_tasks.extract", # task 이름 지정
    queue="docling_queue",                            # task가 등록될 큐 이름
)
# task 함수
def extract(file):
    return {}
