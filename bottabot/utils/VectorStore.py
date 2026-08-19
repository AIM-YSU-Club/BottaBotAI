from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import numpy as np
import huggingface_hub
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document as LangchainDocument
from langchain_ollama import OllamaEmbeddings
from langchain_postgres import PGEngine, PGVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import select

from bottabot.config import settings
from bottabot.db.models import Document, Source, SourceStatus, File
from bottabot.db.session import get_session

# 정규화 기능을 겸하는 임베딩 함수로 상속
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

# 임베딩, 저장, 검색을 수행
class VectorStore:
    """마크다운 청킹, Ollama 임베딩, pgvector 저장을 담당한다."""

    def __init__(self):
        # 텍스트 스플리터 생성
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
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
            metadata_columns=["source_id"],
        )
        # Hugging Face 리랭커: 캐시에 없으면 첫 생성 시 다운로드 후 CrossEncoder 로드
        huggingface_hub.snapshot_download(
            repo_id=settings.HF_RERANKER_MODEL, 
            cache_dir=settings.HF_HOME
        )
        self._reranker = CrossEncoderReranker(
            model=HuggingFaceCrossEncoder(model_name=settings.HF_RERANKER_MODEL),
            top_n=settings.RERANKER_TOP_N,
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


    def similarity_search(self, query: str, notebook_id: uuid.UUID) -> list[dict[str, Any]]:
        """노트북에 속한 소스의 청크만 대상으로 하이브리드 검색 수행."""
        with get_session() as session:
            source_ids = list(
                session.execute(
                    select(Source.source_id).where(Source.notebook_id == notebook_id)
                ).scalars().all()
            )
            if not source_ids:
                logging.info("검색 대상 소스 없음")
                return []

            rows = session.execute(
                select(Document)
                .where(Document.source_id.in_(source_ids))
                .where(Document.chunk.is_not(None))
            ).scalars().all()

            docstore = [
                LangchainDocument(
                    id=str(row.document_id),
                    page_content=row.chunk or "",
                    metadata={
                        "source_id": str(row.source_id),
                        "document_id": str(row.document_id),
                    },
                )
                for row in rows
            ]

        if not docstore:
            logging.info("검색 대상 청크 없음")
            return []

        source_id_strs = [str(sid) for sid in source_ids]
        base_retriever = self._pg_store.as_retriever(
            search_kwargs={
                "k": settings.RETRIEVER_SEARCH_K,
                "filter": {"source_id": {"$in": source_id_strs}},
            }
        )
        # 키워드 기반 검색기 생성
        bm25_retriever = BM25Retriever.from_documents(
            docstore, k=settings.RETRIEVER_SEARCH_K
        )
        # 의미 + 키워드 조합 하이브리드 검색기 생성
        ensemble_retriever = EnsembleRetriever(
            retrievers=[base_retriever, bm25_retriever],
            weights=settings.SK_WEIGHTS,
        )
        # 하이브리드 검색기 + 리랭커
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=self._reranker,
            base_retriever=ensemble_retriever,
        )
        # 검색 수행
        results: list[LangchainDocument] = compression_retriever.invoke(query)

        # 검색 결과가 없으면 빈 리스트 반환
        if not results:
            logging.info("검색 결과 없음")
            return []

        # 검색 결과 출력
        logging.info("**Rerank 검색 결과**")
        for doc in results:
            logging.info(
                f"{doc.metadata.get('name', '이름 없음')} ({doc.id})\n"
                f"내용: {doc.page_content[:100]}..."
            )

        return [
            {
                # PGVectorStore 결과는 id 에, BM25 쪽은 metadata.document_id 에 둘 수 있다.
                "document_id": str(r.metadata.get("document_id") or r.id or ""),
                "chunk": r.page_content,
                "source_id": str(r.metadata.get("source_id") or ""),
            }
            for r in results
            if (r.metadata.get("document_id") or r.id)
        ]

    # 문서에서 추출한 마크다운 원문을 청킹, 임베딩 후 PGVector DB에 저장
    def store_parsed_document(
        self,
        source_id: uuid.UUID,
        file_name: str,
        markdown: str,
        path: str | None = None,
    ) -> dict:
        chunks = self.chunk_markdown(markdown)
        if not chunks:
            raise ValueError("문서에서 저장할 텍스트 청크를 만들지 못했습니다.")

        self._pg_store.add_documents([
            LangchainDocument(
                id=str(uuid.uuid4()),
                page_content=c,
                metadata={
                    'source_id': source_id,
                }
            ) for c in chunks
        ])

        # DB에 파일 정보 저장
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is not None:
                source.chunk_count = len(chunks)
            session.add(
                File(
                    file_id=uuid.uuid4(),
                    source_id=source_id,
                    file_name=file_name,
                    path=path,
                    markdown=markdown,
                )
            )

        return {
            "source_id": str(source_id),
            "chunk_count": len(chunks),
            "status": SourceStatus.DONE,
        }

    # Source 생성 후 PENDING 상태 부여하는 메소드.
    # 문서 분석 시작 전 호출됨.
    def create_pending_source(self, notebook_id: uuid.UUID) -> uuid.UUID:
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
    
    # Source 상태를 FAILED로 업데이트하는 메소드.
    # 문서 분석 도중 예외 발생 시 호출됨.
    def mark_source_done(self, source_id: uuid.UUID) -> None:
        """처리 실패 시 Source 상태를 FAILED로 갱신한다."""
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                return
            source.status = SourceStatus.DONE

    # Source 상태를 FAILED로 업데이트하는 메소드.
    # 문서 분석 도중 예외 발생 시 호출됨.
    def mark_source_failed(self, source_id: uuid.UUID) -> None:
        """처리 실패 시 Source 상태를 FAILED로 갱신한다."""
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                return
            source.status = SourceStatus.FAILED
