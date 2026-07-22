from __future__ import annotations

import uuid, logging
from typing import Any

import ollama
from langchain_ollama import OllamaEmbeddings
import numpy as np

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_postgres.vectorstores import PGVector

from langchain_core.documents import Document as LangchainDocument

from langchain_postgres import PGEngine, PGVectorStore

from sqlalchemy import select

from bottabot.config import settings
from bottabot.db.models import Document, File, Source, SourceStatus
from bottabot.db.session import get_session


class NormalizedOllamaEmbeddings(OllamaEmbeddings):
    def embed_documents(self, texts):
        embeddings = super().embed_documents(texts)
        return [self._normalize(e) for e in embeddings]

    def embed_query(self, text):
        embedding = super().embed_query(text)
        return self._normalize(embedding)

    def _normalize(self, v):
        norm = np.linalg.norm(v)
        if norm == 0:
            return v
        return (v / norm).tolist()

class RerankManager:
    _cross_encoder = None
    @classmethod
    def get_cross_encoder(cls) -> HuggingFaceCrossEncoder:
        """크로스 인코더 가져오기 (싱글톤)"""
        if cls._cross_encoder is None:
            logging.info(f"크로스 인코더 로드 중: {Config.RERANKING_MODEL_PATH}")
            logging.info(f"   디바이스: {Config.DEVICE}")
            
            cls._cross_encoder = HuggingFaceCrossEncoder(
                model_name=Config.RERANKING_MODEL_PATH,
                model_kwargs={'device': Config.DEVICE}
            )
            logging.info("크로스 인코더 로드 완료")
        return cls._cross_encoder

    @classmethod
    def cleanup(cls):
        """메모리 정리"""
        if cls._cross_encoder is not None:
            del cls._cross_encoder
            cls._cross_encoder = None
            
            if Config.DEVICE == 'cuda':
                torch.cuda.empty_cache()
                gc.collect()
                logging.info("GPU 메모리 정리 완료")

class VectorStore:
    """마크다운 청킹, Ollama 임베딩, pgvector 저장을 담당한다."""

    def __init__(
        self,
        *,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
    ) -> None:
        # 텍스트 스플리터 생성
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )
        # Ollama 임베딩 함수 생성
        self._embedding_function = NormalizedOllamaEmbeddings(
            model=settings.OLLAMA_EMBEDDING_MODEL,
            base_url=settings.OLLAMA_URL
        )
        # PGVector DB 엔진 생성
        self._pg_engine = PGEngine.from_connection_string(
            url=f"postgresql+psycopg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}",
        )
        # PGVector Store 생성
        self._pg_store = PGVectorStore.create_sync(
            engine=self._pg_engine,
            table_name="document",
            embedding_service=self._embedding_function,
            id_column="document_id",
            content_column="chunk",
            embedding_column="embeddings",
            metadata_columns=["notebook_id", "source_id"],
        )

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


    def similarity_search(
        self,
        query: str,
        notebook_id: uuid.UUID,
        *,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        notebook 범위에서 코사인 거리(<=>) 기준 top-k 청크를 반환한다.

        반환 항목:
        - document_id: search_map 매핑에 사용
        - chunk: LLM 컨텍스트에 삽입할 본문
        - source_id: 원본 소스 추적용
        - distance: 작을수록 유사 (cosine distance)
        """
        limit = top_k if top_k is not None else settings.RAG_TOP_K
        vectors = self.embed_texts([query])
        if not vectors:
            return []

        query_vector = vectors[0]
        # pgvector SQLAlchemy helper → ORDER BY embeddings <=> :query
        distance = Document.embeddings.cosine_distance(query_vector)

        with get_session() as session:
            rows = session.execute(
                select(
                    Document.document_id,
                    Document.chunk,
                    Document.source_id,
                    distance.label("distance"),
                )
                .where(Document.notebook_id == notebook_id)
                .where(Document.embeddings.is_not(None))
                .order_by(distance)
                .limit(limit)
            ).all()

            return [
                {
                    "document_id": str(row.document_id),
                    "chunk": row.chunk or "",
                    "source_id": str(row.source_id),
                    "distance": float(row.distance) if row.distance is not None else None,
                }
                for row in rows
            ]

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
        notebook_id: uuid.UUID,
        source_id: uuid.UUID,
        file_name: str,
        markdown: str,
        path: str | None = None,
    ) -> dict:
        chunks = self.chunk_markdown(markdown)
        if not chunks:
            raise ValueError("문서에서 저장할 텍스트 청크를 만들지 못했습니다.")

        # Document 저장
        self._pg_store.add_documents([
            LangchainDocument(
                id=str(uuid.uuid4()),
                page_content=c,
                metadata={
                    'notebook_id': notebook_id,
                    'source_id': source_id
                }
            ) for c in chunks
        ])

        # Source 업데이트
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise ValueError(f"Source를 찾을 수 없습니다: {source_id}")

            source.chunk_count = len(chunks)
            source.status = SourceStatus.DONE

        return {
            "source_id": str(source_id),
            "chunk_count": len(chunks),
            "status": SourceStatus.DONE,
        }
