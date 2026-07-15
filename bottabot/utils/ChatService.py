from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import ollama
from sqlalchemy import select

from bottabot.config import settings
from bottabot.db.models import AnswerDetail, Chat, ChatSession, SearchMap
from bottabot.db.session import get_session
from bottabot.utils.VectorStore import VectorStore


class ChatService:
    """
    RAG 채팅 오케스트레이션.

    처리 순서:
    1) chat_session → notebook_id 확인
    2) pgvector 유사도 검색
    3) 최근 대화 로드/요약
    4) chat + search_map 선저장 (질문·출처 매핑)
    5) Ollama 멀티모달 스트리밍
    6) chat.answer 갱신 + answer_detail 저장 (Ollama 메타데이터)
    """

    # 이 길이(문자 수) 이하면 요약 모델 호출 없이 원문 히스토리를 그대로 사용한다.
    _SUMMARY_CHAR_THRESHOLD = 800

    def __init__(
        self,
        *,
        vector_store: VectorStore | None = None,
        ollama_url: str | None = None,
    ) -> None:
        self._vector_store = vector_store or VectorStore()
        self._ollama_client = ollama.Client(host=ollama_url or settings.OLLAMA_URL)

    def get_chat_session(self, chat_session_id: uuid.UUID) -> ChatSession:
        """chat_session_id로 세션을 조회하고, 세션 종료 후에도 쓸 수 있게 detach한다."""
        with get_session() as session:
            chat_session = session.get(ChatSession, chat_session_id)
            if chat_session is None:
                raise ValueError(f"chat_session을 찾을 수 없습니다: {chat_session_id}")
            session.expunge(chat_session)
            return chat_session

    def load_recent_chats(self, chat_session_id: uuid.UUID) -> list[Chat]:
        """
        세션의 최근 대화를 CHAT_HISTORY_LIMIT개까지 가져온다.
        created_at 기준 최신순으로 조회한 뒤, 요약용으로 시간 오름차순으로 뒤집는다.
        """
        limit = settings.CHAT_HISTORY_LIMIT
        with get_session() as session:
            rows = session.execute(
                select(Chat)
                .where(Chat.chat_session_id == chat_session_id)
                .order_by(Chat.created_at.desc())
                .limit(limit)
            ).scalars().all()

            chats = list(reversed(rows))
            for chat in chats:
                session.expunge(chat)
            return chats

    def format_history_text(self, chats: list[Chat]) -> str:
        """요약 모델 입력용으로 User/Assistant 턴을 평문 대화 로그로 만든다."""
        if not chats:
            return ""

        lines: list[str] = []
        for chat in chats:
            question = (chat.question or "").strip()
            answer = (chat.answer or "").strip()
            if question:
                lines.append(f"User: {question}")
            if answer:
                lines.append(f"Assistant: {answer}")
        return "\n".join(lines)

    def summarize_history(self, history_text: str) -> str:
        """
        최근 대화 맥락을 압축한다.
        - 비어 있으면 placeholder
        - 짧으면 원문 유지 (토큰/지연 절약)
        - 길면 OLLAMA_SUMMARY_LLM(gemma3:1b)로 요약
        """
        cleaned = history_text.strip()
        if not cleaned:
            return "이전 대화 없음."

        if len(cleaned) <= self._SUMMARY_CHAR_THRESHOLD:
            return cleaned

        response = self._ollama_client.chat(
            model=settings.OLLAMA_SUMMARY_LLM,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "다음 대화를 짧고 정확하게 요약하세요. "
                        "핵심 질문, 결정사항, 미해결 이슈만 남기세요."
                    ),
                },
                {
                    "role": "user",
                    "content": cleaned,
                },
            ],
        )
        summary = self._extract_message_content(response).strip()
        return summary or cleaned

    @staticmethod
    def _extract_message_content(chunk: Any) -> str:
        """ollama Client가 dict/객체 중 어떤 형태를 주더라도 message.content를 꺼낸다."""
        if chunk is None:
            return ""
        if isinstance(chunk, dict):
            message = chunk.get("message") or {}
            if isinstance(message, dict):
                return str(message.get("content") or "")
            return str(getattr(message, "content", "") or "")
        message = getattr(chunk, "message", None)
        if message is None:
            return ""
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(getattr(message, "content", "") or "")

    @staticmethod
    def _ns_to_ms(value: Any) -> int | None:
        """Ollama duration(ns) → answer_detail duration(ms)."""
        if value is None:
            return None
        try:
            return int(value) // 1_000_000
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_ollama_metrics(chunk: Any) -> dict[str, Any]:
        """
        스트리밍 마지막 청크에 실리는 Ollama 메타데이터를 정규화한다.
        - prompt_eval_count / eval_count: 입·출력 토큰 수
        - prompt_eval_duration / eval_duration: 입·출력 처리 시간(ns)
        """
        def _get(name: str) -> Any:
            if isinstance(chunk, dict):
                return chunk.get(name)
            return getattr(chunk, name, None)

        return {
            "model": _get("model"),
            "done": bool(_get("done")),
            "input_token_count": _get("prompt_eval_count"),
            "output_token_count": _get("eval_count"),
            "input_duration_ms": ChatService._ns_to_ms(_get("prompt_eval_duration")),
            "output_duration_ms": ChatService._ns_to_ms(_get("eval_duration")),
        }

    def format_retrieved_context(self, hits: list[dict[str, Any]]) -> str:
        """검색된 청크를 LLM이 읽기 쉬운 번호 목록으로 포맷한다."""
        if not hits:
            return "검색된 관련 문서가 없습니다."

        blocks: list[str] = []
        for i, hit in enumerate(hits, start=1):
            chunk = (hit.get("chunk") or "").strip()
            document_id = hit.get("document_id", "")
            blocks.append(f"[{i}] document_id={document_id}\n{chunk}")
        return "\n\n".join(blocks)

    def build_user_prompt(
        self,
        *,
        instructions: str,
        retrieved_context: str,
        conversation_context: str,
        prompt: str,
        image_count: int,
    ) -> str:
        """지침·검색결과·대화요약·이미지 안내·질문을 하나의 user 메시지로 묶는다."""
        image_note = (
            f"첨부 이미지 {image_count}장이 함께 제공되었습니다. 이미지 내용을 참고하세요."
            if image_count > 0
            else "첨부 이미지 없음."
        )
        return (
            f"[지침]\n{instructions.strip()}\n\n"
            f"[검색된 문서 컨텍스트]\n{retrieved_context.strip()}\n\n"
            f"[최근 대화 맥락]\n{conversation_context.strip()}\n\n"
            f"[첨부 이미지]\n{image_note}\n\n"
            f"[사용자 질문]\n{prompt.strip()}"
        )

    def create_chat_with_search_map(
        self,
        *,
        chat_session_id: uuid.UUID,
        question: str,
        document_ids: list[uuid.UUID],
    ) -> uuid.UUID:
        """
        스트리밍 시작 전에 chat(질문)과 search_map(출처 문서)을 먼저 커밋한다.
        answer는 아직 비워 두고, 스트림 완료 후 finalize_chat_answer에서 채운다.
        """
        chat_id = uuid.uuid4()
        with get_session() as session:
            session.add(
                Chat(
                    chat_id=chat_id,
                    chat_session_id=chat_session_id,
                    question=question,
                    answer=None,
                )
            )
            # chat FK를 만족시키기 위해 chat을 먼저 flush한 뒤 search_map을 넣는다.
            session.flush()

            seen: set[uuid.UUID] = set()
            for document_id in document_ids:
                if document_id in seen:
                    continue
                seen.add(document_id)
                session.add(
                    SearchMap(
                        chat_id=chat_id,
                        document_id=document_id,
                    )
                )
        return chat_id

    def finalize_chat_answer(
        self,
        *,
        chat_id: uuid.UUID,
        answer: str,
        conversation_context: str,
        instruction: str,
        metrics: dict[str, Any],
    ) -> None:
        """
        스트리밍이 끝난 뒤:
        - chat.answer에 최종 응답 본문 저장
        - answer_detail에 Ollama 메타데이터 + 사용 지침/대화맥락 저장
        """
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if chat is None:
                raise ValueError(f"chat을 찾을 수 없습니다: {chat_id}")

            chat.answer = answer

            session.add(
                AnswerDetail(
                    chat_id=chat_id,
                    model=str(metrics.get("model") or settings.OLLAMA_CHAT_LLM),
                    context=conversation_context,
                    instruction=instruction,
                    input_token_count=(
                        int(metrics["input_token_count"])
                        if metrics.get("input_token_count") is not None
                        else None
                    ),
                    output_token_count=(
                        int(metrics["output_token_count"])
                        if metrics.get("output_token_count") is not None
                        else None
                    ),
                    input_duration=metrics.get("input_duration_ms"),
                    output_duration=metrics.get("output_duration_ms"),
                )
            )

    def stream_chat(
        self,
        *,
        chat_session_id: uuid.UUID,
        prompt: str,
        images: list[bytes] | None = None,
    ) -> Iterator[str]:
        """
        RAG 파이프라인을 수행하고 답변 토큰을 yield한다.
        부수 효과로 chat / search_map / answer_detail 레코드를 DB에 남긴다.
        """
        image_bytes = images or []
        if len(image_bytes) > 5:
            raise ValueError("이미지는 최대 5장까지 첨부할 수 있습니다.")

        # 1) 세션 → 노트북 범위 결정 (검색 스코프)
        chat_session = self.get_chat_session(chat_session_id)
        notebook_id = chat_session.notebook_id

        # 2) 질문 임베딩 + notebook 한정 유사도 검색
        hits = self._vector_store.similarity_search(prompt, notebook_id)
        retrieved_context = self.format_retrieved_context(hits)

        # 3) 최근 대화 요약(또는 원문)으로 맥락 압축
        chats = self.load_recent_chats(chat_session_id)
        history_text = self.format_history_text(chats)
        conversation_context = self.summarize_history(history_text)

        instruction = settings.RAG_SYSTEM_INSTRUCTIONS
        user_prompt = self.build_user_prompt(
            instructions=instruction,
            retrieved_context=retrieved_context,
            conversation_context=conversation_context,
            prompt=prompt,
            image_count=len(image_bytes),
        )

        # 4) 질문/출처를 먼저 저장 (스트림 도중 끊겨도 질문·검색 근거는 남김)
        document_ids: list[uuid.UUID] = []
        for hit in hits:
            raw_id = hit.get("document_id")
            if not raw_id:
                continue
            document_ids.append(uuid.UUID(str(raw_id)))

        chat_id = self.create_chat_with_search_map(
            chat_session_id=chat_session_id,
            question=prompt,
            document_ids=document_ids,
        )

        message: dict[str, Any] = {
            "role": "user",
            "content": user_prompt,
        }
        if image_bytes:
            # Ollama multimodal: bytes 또는 base64 문자열을 images에 전달
            message["images"] = image_bytes

        # 5) 본 답변 스트리밍 (gemma3)
        stream = self._ollama_client.chat(
            model=settings.OLLAMA_CHAT_LLM,
            messages=[message],
            stream=True,
        )

        answer_parts: list[str] = []
        metrics: dict[str, Any] = {
            "model": settings.OLLAMA_CHAT_LLM,
            "input_token_count": None,
            "output_token_count": None,
            "input_duration_ms": None,
            "output_duration_ms": None,
        }

        try:
            for chunk in stream:
                content = self._extract_message_content(chunk)
                if content:
                    answer_parts.append(content)
                    yield content

                # done=true 인 마지막 청크에서 토큰/시간 메타데이터를 수집
                chunk_metrics = self._extract_ollama_metrics(chunk)
                if chunk_metrics.get("done") or chunk_metrics.get("output_token_count") is not None:
                    for key in (
                        "model",
                        "input_token_count",
                        "output_token_count",
                        "input_duration_ms",
                        "output_duration_ms",
                    ):
                        if chunk_metrics.get(key) is not None:
                            metrics[key] = chunk_metrics[key]
        finally:
            # 6) 스트림이 정상/예외로 끝나도 지금까지의 답변과 메타데이터를 커밋 시도
            final_answer = "".join(answer_parts)
            if final_answer:
                self.finalize_chat_answer(
                    chat_id=chat_id,
                    answer=final_answer,
                    conversation_context=conversation_context,
                    instruction=instruction,
                    metrics=metrics,
                )
