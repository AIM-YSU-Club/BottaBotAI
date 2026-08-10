from bottabot.celery.app import celery_app
from io import BytesIO
import base64
import uuid


@celery_app.task(
    name="bottabot.celery.tasks.docling_tasks.docling_parse",
    queue="docling_queue",
)
def docling_parse(encoded_file: str, file_name: str, notebook_id: str):
    from bottabot.utils.DocParse import DoclingParser
    from bottabot.utils.VectorStore import VectorStore

    notebook_uuid = uuid.UUID(notebook_id)
    vector_store = VectorStore()
    source_id = vector_store.create_pending_source(notebook_uuid)

    try:
        file_bytes = base64.b64decode(encoded_file)

        dp = DoclingParser(BytesIO(file_bytes), file_name)
        dp.process_file()

        if not dp.parsed_markdown or not dp.parsed_markdown.strip():
            raise ValueError(f"문서 파싱 결과가 비어 있습니다: {file_name}")

        result = vector_store.store_parsed_document(
            source_id=source_id,
            file_name=file_name,
            markdown=dp.parsed_markdown,
            notebook_id=notebook_uuid,
        )

        print(
            f"문서 저장 완료: file={file_name}, "
            f"source_id={result['source_id']}, chunks={result['chunk_count']}, "
            f"status={result['status']}"
        )

        vector_store.mark_source_done(source_id)

        return result
    except Exception:
        vector_store.mark_source_failed(source_id)
        raise
