import uuid
from enum import StrEnum
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
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
    """벡터화를 위해 쪼개진 문서 청크."""

    __tablename__ = "document"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebook.notebook_id"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source.source_id"),
        nullable=False,
    )
    chunk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embeddings: Mapped[Optional[list[float]]] = mapped_column(
        Vector(),
        nullable=True,
    )
