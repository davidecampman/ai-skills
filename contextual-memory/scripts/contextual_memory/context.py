from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import ContextPacket, MemoryCandidate, MemoryRecord, Message, SessionState, TurnRecord


DEFAULT_SYSTEM_BRIEF = "Use relevant confirmed context. Treat staged memories as unconfirmed."


@dataclass(slots=True)
class ContextPacker:
    system_brief: str = DEFAULT_SYSTEM_BRIEF

    def build(
        self,
        session_state: SessionState,
        user_message: str,
        retrieved_memories: list[MemoryRecord],
        recent_turns: list[TurnRecord],
        staged_memories: list[MemoryCandidate] | None = None,
        max_context_tokens: int = 6000,
    ) -> ContextPacket:
        staged_memories = staged_memories or []
        accepted: list[tuple[str, str]] = []
        dropped: list[str] = []
        used = 0

        def add(label: str, text: str, required: bool = False) -> str:
            nonlocal used
            if not text:
                return ""
            remaining = max_context_tokens - used
            if remaining <= 0:
                dropped.append(label)
                return ""
            tokens = approximate_tokens(text)
            if tokens > remaining:
                if not required:
                    dropped.append(label)
                    return ""
                text = trim_to_tokens(text, remaining)
                tokens = approximate_tokens(text)
            accepted.append((label, text))
            used += tokens
            return text

        system_text = add("system_brief", self.system_brief, required=True)
        add("session_state", _format_session(session_state), required=True)
        packed_user_message = add("current_user_message", user_message, required=True)

        accepted_memories = []
        for memory in retrieved_memories:
            label = f"memory:{memory.memory_id or memory.kind}"
            before = used
            add(label, _format_memory(memory))
            if used > before:
                accepted_memories.append(memory)

        accepted_turns = []
        for turn in recent_turns:
            label = f"turn:{turn.turn_id or turn.created_at}"
            before = used
            add(label, _format_turn(turn))
            if used > before:
                accepted_turns.append(turn)

        accepted_staged = []
        for candidate in staged_memories:
            label = f"staged:{candidate.candidate_id or candidate.record.kind}"
            before = used
            add(label, _format_candidate(candidate))
            if used > before:
                accepted_staged.append(candidate)

        context_sections = [text for label, text in accepted if label != "current_user_message"]
        messages = [
            Message(role="system", content="\n\n".join(context_sections)),
            Message(role="user", content=packed_user_message),
        ]
        report = {
            "max_tokens": max_context_tokens,
            "used_tokens": sum(approximate_tokens(message.content) for message in messages),
            "dropped": dropped,
            "included": [label for label, _ in accepted],
        }
        return ContextPacket(
            system_brief=system_text,
            session_state=session_state,
            retrieved_memories=accepted_memories,
            recent_turns=accepted_turns,
            staged_memories=accepted_staged,
            token_budget_report=report,
            messages=messages,
        )


def approximate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def trim_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    max_chars = max(1, max_tokens * 4)
    if len(text) <= max_chars:
        return text
    suffix = " [truncated]"
    return text[: max(1, max_chars - len(suffix))] + suffix


def total_message_tokens(messages: Iterable[Message]) -> int:
    return sum(approximate_tokens(message.content) for message in messages)


def _format_session(state: SessionState) -> str:
    return "\n".join(
        [
            "Session state:",
            f"Goal: {state.current_goal or 'unknown'}",
            f"Summary: {state.rolling_summary or 'none'}",
            f"Decisions: {', '.join(state.decisions) if state.decisions else 'none'}",
            f"Open: {', '.join(state.open_questions) if state.open_questions else 'none'}",
        ]
    )


def _format_memory(memory: MemoryRecord) -> str:
    return f"Memory [{memory.kind}; c={memory.confidence:.2f}; i={memory.importance:.2f}]: {memory.text}"


def _format_turn(turn: TurnRecord) -> str:
    return f"Recent turn:\nUser: {turn.user_message}\nAssistant: {turn.assistant_message}"


def _format_candidate(candidate: MemoryCandidate) -> str:
    return (
        f"Staged memory candidate [{candidate.record.kind}; action={candidate.action}]: "
        f"{candidate.record.text}"
    )
