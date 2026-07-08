from bottabot.celery.app import celery_app
from io import BytesIO
import base64

# task 함수 예시
@celery_app.task(
    name="bottabot.celery.tasks.docling_tasks.docling_parse", # task 이름 지정
    queue="docling_queue",                            # task가 등록될 큐 이름
)
# task 함수
def docling_parse(encoded_file: str, file_name: str):
    from bottabot.utils.DocParse import DoclingParser
    bytes = base64.b64decode(encoded_file)

    dp = DoclingParser(BytesIO(bytes), file_name)
    dp.process_file()

    print(dp.parsed_markdown)

    return {}
