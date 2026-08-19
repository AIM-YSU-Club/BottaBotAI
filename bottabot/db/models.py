import uuid
from datetime import datetime
from enum import StrEnum
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


class Notebook(Base):
    """노트북 — RAG 챗봇의 기본 단위(지식베이스/프로젝트)."""

    __tablename__ = "notebook"

    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    notebook_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )


class Source(Base):
    """소스 — RAG에 올린 원본(파일·웹페이지·이미지 등)의 메타데이터."""

    __tablename__ = "source"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebook.notebook_id"),
        nullable=False,
    )
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=SourceStatus.PENDING,
        server_default=SourceStatus.PENDING,
    )


class File(Base):
    """파일 — Source에 연결된 물리 파일 메타데이터 및 마크다운 원문."""

    __tablename__ = "file"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.source_id"),
        nullable=False,
    )
    file_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Document(Base):
    """벡터화를 위해 쪼개진 문서 청크. Notebook에만 소속되며 Source FK는 두지 않는다."""

    __tablename__ = "document"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebook.notebook_id"),
        nullable=False,
    )
    chunk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embeddings: Mapped[Optional[list[float]]] = mapped_column(
        Vector(),
        nullable=True,
    )


class ChatSession(Base):
    """
    대화 세션(대화창). 노트북 단위로 여러 세션을 가질 수 있다.
    소유 사용자는 notebook → user 경로로 식별한다 (chat_session.user_id 컬럼 없음).
    """

    __tablename__ = "chat_session"

    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebook.notebook_id"),
        nullable=False,
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Chat(Base):
    """질문-응답 쌍. AnswerDetail과 1:1, SearchMap을 통해 Document와 N:M."""

    __tablename__ = "chat"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_session.chat_session_id"),
        nullable=False,
    )
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SearchMap(Base):
    """
    질문(Chat)과 유사도 검색으로 선택된 Document 청크의 N:M 매핑.
    프론트/백엔드에서 답변 출처(citation)를 표시할 때 사용한다.
    복합 PK: (chat_id, document_id)
    """

    __tablename__ = "search_map"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat.chat_id"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.document_id"),
        primary_key=True,
    )


class AnswerDetail(Base):
    """
    챗봇 답변 상세 — Chat과 1:1 (공유 PK = chat_id).
    Ollama 스트리밍 종료 시 제공되는 모델명·토큰 수·처리 시간 등을 저장한다.
    duration 컬럼 단위는 Java 엔티티와 동일하게 millisecond.
    """

    __tablename__ = "answer_detail"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat.chat_id"),
        primary_key=True,
    )
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Ollama는 ns를 반환하므로 저장 전에 ms로 변환한다.
    input_duration: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    output_duration: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
