from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from .models import CompletedTurn, MemoryExtractor, MemoryWriteSet, Message


@dataclass(slots=True)
class StaticChatModel:
    response: str = "Acknowledged."
    calls: list[list[Message]] = field(default_factory=list)

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        return self.response


@dataclass(slots=True)
class EchoChatModel:
    calls: list[list[Message]] = field(default_factory=list)

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        user = next((message.content for message in reversed(messages) if message.role == "user"), "")
        return f"Echo: {user}"


@dataclass(slots=True)
class StaticMemoryExtractor(MemoryExtractor):
    write_set: MemoryWriteSet = field(default_factory=MemoryWriteSet)
    calls: list[CompletedTurn] = field(default_factory=list)

    def extract(self, turn: CompletedTurn) -> MemoryWriteSet:
        self.calls.append(turn)
        return self.write_set


class HashingEmbedder:
    def embed(self, text: str) -> list[float]:
        digest = sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:16]]
