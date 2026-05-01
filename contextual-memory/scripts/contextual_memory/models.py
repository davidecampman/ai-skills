from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class MemoryAction(StrEnum):
    COMMIT = "commit"
    STAGE = "stage"
    IGNORE = "ignore"
    MARK_CONFLICT = "mark_conflict"


class CandidateStatus(StrEnum):
    STAGED = "staged"
    COMMITTED = "committed"
    IGNORED = "ignored"


@dataclass(slots=True)
class Message:
    role: str
    content: str


@dataclass(slots=True)
class SessionState:
    session_id: str
    user_id: str = "default"
    current_goal: str = ""
    rolling_summary: str = ""
    decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: now_iso())


@dataclass(slots=True)
class MemoryRecord:
    kind: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "assistant_inferred"
    confidence: float = 0.5
    importance: float = 0.5
    created_at: str = field(default_factory=lambda: now_iso())
    updated_at: str = field(default_factory=lambda: now_iso())
    stale: bool = False
    user_id: str = "default"
    memory_id: str | None = None


@dataclass(slots=True)
class MemoryChunk:
    memory_id: str
    user_id: str
    text: str
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    stale: bool = False
    created_at: str = field(default_factory=lambda: now_iso())
    chunk_id: str | None = None


@dataclass(slots=True)
class EmbeddingRecord:
    chunk_id: str
    memory_id: str
    user_id: str
    provider: str
    model: str
    dimensions: int
    embedding: list[float]
    created_at: str = field(default_factory=lambda: now_iso())
    updated_at: str = field(default_factory=lambda: now_iso())
    embedding_id: int | None = None


@dataclass(slots=True)
class MemorySearchResult:
    memory: MemoryRecord
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0
    sources: list[str] = field(default_factory=list)
    chunk_id: str | None = None


@dataclass(slots=True)
class MemoryCandidate:
    record: MemoryRecord
    action: MemoryAction | str = MemoryAction.STAGE
    conflict_memory_ids: list[str] = field(default_factory=list)
    reason: str = ""
    candidate_id: str | None = None


@dataclass(slots=True)
class TurnRecord:
    session_id: str
    user_id: str
    user_message: str
    assistant_message: str
    context_summary: str = ""
    created_at: str = field(default_factory=lambda: now_iso())
    turn_id: int | None = None


@dataclass(slots=True)
class CompletedTurn:
    session_state: SessionState
    user_message: str
    assistant_message: str
    context_packet: ContextPacket
    turn_id: int | None = None
    created_at: str = field(default_factory=lambda: now_iso())


@dataclass(slots=True)
class MemoryWriteSet:
    candidates: list[MemoryCandidate] = field(default_factory=list)


@dataclass(slots=True)
class ContextPacket:
    system_brief: str
    session_state: SessionState
    retrieved_memories: list[MemoryRecord] = field(default_factory=list)
    recent_turns: list[TurnRecord] = field(default_factory=list)
    staged_memories: list[MemoryCandidate] = field(default_factory=list)
    token_budget_report: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class ConversationTurnResult:
    assistant_text: str
    context_packet: ContextPacket
    committed_memories: list[MemoryRecord] = field(default_factory=list)
    staged_memories: list[MemoryCandidate] = field(default_factory=list)
    session_state: SessionState | None = None
    turn_id: int | None = None


class ChatModel(Protocol):
    def complete(self, messages: list[Message]) -> str:
        raise NotImplementedError


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MemoryExtractor(Protocol):
    def extract(self, turn: CompletedTurn) -> MemoryWriteSet:
        raise NotImplementedError


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def to_dict(value: Any) -> dict[str, Any]:
    data = asdict(value)
    for key, item in list(data.items()):
        if isinstance(item, StrEnum):
            data[key] = item.value
    return data
