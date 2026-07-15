from __future__ import annotations

import uuid

import ollama
from langchain_text_splitters import RecursiveCharacterTextSplitter

from bottabot.config import settings
from bottabot.db.models import Document, File, Source, SourceStatus
from bottabot.db.session import get_session


class VectorStore:
    """마크다운 청킹, Ollama 임베딩, pgvector 저장을 담당한다."""

    def __init__(
        self,
        *,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
        ollama_url: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )
        self._ollama_url = ollama_url or settings.OLLAMA_URL
        self._embedding_model = embedding_model or settings.OLLAMA_EMBEDDING_MODEL
        self._ollama_client = ollama.Client(host=self._ollama_url)

    # Langchain의 RecursiveTextSplitter로 청킹 수행
    def chunk_markdown(self, text: str) -> list[str]:
        cleaned = text.strip()
        if not cleaned:
            return []

        return [
            chunk.strip()
            for chunk in self._text_splitter.split_text(cleaned)
            if chunk.strip()
        ]

    # Ollama에 임베딩 요청을 보내고 응답으로 받은 벡터를 반환
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings: list[list[float]] = []
        for text in texts:
            response = self._ollama_client.embeddings(
                model=self._embedding_model,
                prompt=text,
            )
            vector = response["embedding"]
            if not vector:
                raise RuntimeError("Ollama returned an empty embedding vector")
            embeddings.append(vector)

        return embeddings

    def create_pending_source(self, notebook_id: uuid.UUID) -> uuid.UUID:
        """태스크 시작 시 PENDING 상태의 Source를 생성한다."""
        source_id = uuid.uuid4()
        with get_session() as session:
            session.add(
                Source(
                    source_id=source_id,
                    notebook_id=notebook_id,
                    status=SourceStatus.PENDING,
                )
            )
        return source_id

    def mark_source_failed(self, source_id: uuid.UUID) -> None:
        """처리 실패 시 Source 상태를 FAILED로 갱신한다."""
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                return
            source.status = SourceStatus.FAILED

    # 문서에서 추출한 마크다운 원문을 청킹, 임베딩 후 PGVector DB에 저장
    def store_parsed_document(
        self,
        *,
        source_id: uuid.UUID,
        file_name: str,
        markdown: str,
        notebook_id: uuid.UUID,
        path: str | None = None,
    ) -> dict:
        chunks = self.chunk_markdown(markdown)
        if not chunks:
            raise ValueError("문서에서 저장할 텍스트 청크를 만들지 못했습니다.")

        vectors = self.embed_texts(chunks)
        if len(vectors) != len(chunks):
            raise RuntimeError("청크 수와 임베딩 수가 일치하지 않습니다.")

        file_id = uuid.uuid4()
        document_ids: list[str] = []

        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise ValueError(f"Source를 찾을 수 없습니다: {source_id}")

            session.add(
                File(
                    file_id=file_id,
                    file_name=file_name,
                    markdown=markdown,
                    path=path or file_name,
                    source_id=source_id,
                )
            )

            for chunk, vector in zip(chunks, vectors):
                document_id = uuid.uuid4()
                session.add(
                    Document(
                        document_id=document_id,
                        chunk=chunk,
                        embeddings=vector,
                        notebook_id=notebook_id,
                        source_id=source_id,
                    )
                )
                document_ids.append(str(document_id))

            source.chunk_count = len(chunks)
            source.status = SourceStatus.DONE

        return {
            "source_id": str(source_id),
            "file_id": str(file_id),
            "document_ids": document_ids,
            "chunk_count": len(chunks),
            "status": SourceStatus.DONE,
        }
