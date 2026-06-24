from bottabot.celery.app import celery_app

# task 함수 예시
@celery_app.task(
    name="bottabot.celery.tasks.reranker.hello_world", # task 이름 지정
    queue="reranker_queue",                            # task가 등록될 큐 이름
)
# task 함수
def hello_world():
    return {"hello": "world", "status": "pending"}
