from __future__ import annotations

from dataclasses import dataclass

from .context import ContextPacker
from .models import (
    ChatModel,
    CompletedTurn,
    ConversationTurnResult,
    MemoryAction,
    MemoryCandidate,
    MemoryExtractor,
    MemoryRecord,
    MemoryWriteSet,
    SessionState,
    TurnRecord,
    now_iso,
)
from .store import SQLiteMemoryStore


SAFE_AUTO_COMMIT_SOURCES = {"user_stated", "tool_verified"}


@dataclass(slots=True)
class ConversationOrchestrator:
    store: SQLiteMemoryStore
    chat_model: ChatModel
    memory_extractor: MemoryExtractor
    packer: ContextPacker | None = None
    retrieval_limit: int = 8
    recent_turn_limit: int = 6
    auto_commit_confidence: float = 0.75

    def run_turn(
        self,
        session_id: str,
        user_message: str,
        user_id: str = "default",
        max_context_tokens: int = 6000,
    ) -> ConversationTurnResult:
        packer = self.packer or ContextPacker()
        session = self.store.get_session(session_id, user_id=user_id)
        memories = self.store.search_memories(
            user_message,
            user_id=user_id,
            limit=self.retrieval_limit,
            include_stale=False,
        )
        recent_turns = self.store.recent_turns(
            session_id,
            user_id=user_id,
            limit=self.recent_turn_limit,
        )
        staged_candidates = self.store.list_candidates(user_id=user_id)
        context_packet = packer.build(
            session,
            user_message,
            memories,
            recent_turns,
            staged_candidates,
            max_context_tokens=max_context_tokens,
        )
        assistant_text = self.chat_model.complete(context_packet.messages)
        session = self._update_session(session, user_message, assistant_text)
        self.store.save_session(session)
        turn_id = self.store.add_turn(
            TurnRecord(
                session_id=session_id,
                user_id=user_id,
                user_message=user_message,
                assistant_message=assistant_text,
                context_summary=self._context_summary(context_packet),
            )
        )
        completed_turn = CompletedTurn(
            session_state=session,
            user_message=user_message,
            assistant_message=assistant_text,
            context_packet=context_packet,
            turn_id=turn_id,
        )
        write_set = self.memory_extractor.extract(completed_turn)
        committed, staged = self._apply_write_set(write_set, user_id=user_id)
        return ConversationTurnResult(
            assistant_text=assistant_text,
            context_packet=context_packet,
            committed_memories=committed,
            staged_memories=staged,
            session_state=session,
            turn_id=turn_id,
        )

    def _apply_write_set(
        self,
        write_set: MemoryWriteSet,
        user_id: str,
    ) -> tuple[list[MemoryRecord], list[MemoryCandidate]]:
        committed: list[MemoryRecord] = []
        staged: list[MemoryCandidate] = []
        for candidate in write_set.candidates:
            candidate.record.user_id = user_id
            action = MemoryAction(candidate.action)
            if action is MemoryAction.IGNORE:
                continue
            conflicts = self.store.find_conflicts(candidate.record)
            conflict_ids = [item.memory_id for item in conflicts if item.memory_id]
            candidate.conflict_memory_ids = sorted(
                {*candidate.conflict_memory_ids, *conflict_ids}
            )
            if self._should_mark_conflict(candidate):
                for memory_id in candidate.conflict_memory_ids:
                    self.store.mark_stale(memory_id)
                committed.append(self.store.insert_memory(candidate.record))
                continue
            if self._should_auto_commit(candidate):
                committed.append(self.store.insert_memory(candidate.record))
                continue
            if not candidate.reason:
                candidate.reason = self._stage_reason(candidate)
            staged.append(self.store.stage_candidate(candidate))
        return committed, staged

    def _should_auto_commit(self, candidate: MemoryCandidate) -> bool:
        action = MemoryAction(candidate.action)
        return (
            action is MemoryAction.COMMIT
            and not candidate.conflict_memory_ids
            and candidate.record.source in SAFE_AUTO_COMMIT_SOURCES
            and candidate.record.confidence >= self.auto_commit_confidence
        )

    def _should_mark_conflict(self, candidate: MemoryCandidate) -> bool:
        action = MemoryAction(candidate.action)
        return (
            action is MemoryAction.MARK_CONFLICT
            and bool(candidate.conflict_memory_ids)
            and candidate.record.source in SAFE_AUTO_COMMIT_SOURCES
            and candidate.record.confidence >= self.auto_commit_confidence
        )

    def _stage_reason(self, candidate: MemoryCandidate) -> str:
        if candidate.conflict_memory_ids:
            return "Conflicts with existing memory."
        if candidate.record.source not in SAFE_AUTO_COMMIT_SOURCES:
            return "Memory source is not safe for auto-commit."
        if candidate.record.confidence < self.auto_commit_confidence:
            return "Memory confidence is below auto-commit threshold."
        return "Memory candidate requires review."

    def _update_session(
        self,
        session: SessionState,
        user_message: str,
        assistant_text: str,
    ) -> SessionState:
        if not session.current_goal:
            session.current_goal = _compact(user_message, 120)
        line = f"User asked: {_compact(user_message, 90)} | Assistant answered: {_compact(assistant_text, 90)}"
        if session.rolling_summary:
            session.rolling_summary = _compact(f"{session.rolling_summary}\n{line}", 1200)
        else:
            session.rolling_summary = line
        session.updated_at = now_iso()
        return session

    def _context_summary(self, packet) -> str:
        report = packet.token_budget_report
        return (
            f"used={report.get('used_tokens', 0)}/{report.get('max_tokens', 0)}; "
            f"memories={len(packet.retrieved_memories)}; turns={len(packet.recent_turns)}; "
            f"dropped={','.join(report.get('dropped', []))}"
        )


def _compact(text: str, max_chars: int) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= max_chars:
        return single_line
    return single_line[: max_chars - 12] + " [truncated]"
