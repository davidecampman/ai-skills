from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contextual_memory import (
    ContextPacker,
    ConversationOrchestrator,
    HashingEmbedder,
    MemoryAction,
    MemoryCandidate,
    MemoryRecord,
    MemoryWriteSet,
    OllamaEmbedder,
    SQLiteMemoryStore,
    SessionState,
    StaticChatModel,
    StaticMemoryExtractor,
    TurnRecord,
    total_message_tokens,
)


class ContextualMemoryTests(unittest.TestCase):
    def test_sqlite_round_trip_for_sessions_memories_candidates_and_stale_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
            session = SessionState(
                session_id="s1",
                user_id="u1",
                current_goal="Design a memory system",
                decisions=["Use SQLite"],
            )
            store.save_session(session)
            self.assertEqual(store.get_session("s1", "u1").decisions, ["Use SQLite"])

            memory = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="fact",
                    text="The user prefers compact context packets.",
                    source="user_stated",
                    confidence=0.95,
                    importance=0.8,
                )
            )
            self.assertIsNotNone(memory.memory_id)
            self.assertIn("compact context", store.get_memory(memory.memory_id).text)

            candidate = store.stage_candidate(
                MemoryCandidate(
                    record=MemoryRecord(
                        user_id="u1",
                        kind="preference",
                        text="The user may prefer terse answers.",
                        source="assistant_inferred",
                        confidence=0.55,
                    ),
                    action=MemoryAction.STAGE,
                )
            )
            self.assertEqual(store.list_candidates("u1")[0].candidate_id, candidate.candidate_id)

            store.mark_stale(memory.memory_id)
            self.assertEqual(store.list_memories("u1"), [])
            self.assertTrue(store.list_memories("u1", include_stale=True)[0].stale)

    def test_retrieval_ranks_relevant_fresh_confirmed_memories_and_filters_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
            stale = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="fact",
                    text="The user uses Pinecone for memory storage.",
                    source="user_stated",
                    confidence=0.95,
                    importance=1.0,
                )
            )
            store.mark_stale(stale.memory_id)
            sqlite = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="decision",
                    text="The memory architecture uses SQLite first for local retrieval.",
                    source="user_stated",
                    confidence=0.95,
                    importance=0.9,
                )
            )
            store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="episode",
                    text="Discussed dashboard filters for job applications.",
                    source="assistant_inferred",
                    confidence=0.6,
                    importance=0.3,
                )
            )

            results = store.search_memories("SQLite memory retrieval", user_id="u1")
            self.assertEqual(results[0].memory_id, sqlite.memory_id)
            self.assertTrue(all(not memory.stale for memory in results))
            self.assertNotIn(stale.memory_id, [memory.memory_id for memory in results])

    def test_context_packer_respects_budget_and_keeps_high_priority_context(self):
        session = SessionState(
            session_id="s1",
            current_goal="Keep an ongoing LLM conversation contextual.",
            rolling_summary="Earlier discussion chose SQLite and provider interfaces.",
        )
        memories = [
            MemoryRecord(kind="decision", text="Use SQLite first.", confidence=0.9, importance=0.9),
            MemoryRecord(kind="episode", text="Long filler " * 200, confidence=0.5, importance=0.1),
        ]
        turns = [
            TurnRecord(
                session_id="s1",
                user_id="default",
                user_message="Previous question",
                assistant_message="Previous answer",
            )
        ]
        packet = ContextPacker().build(
            session,
            "How should retrieval work?",
            memories,
            turns,
            max_context_tokens=95,
        )
        self.assertLessEqual(total_message_tokens(packet.messages), 95)
        self.assertIn("Session state", packet.messages[0].content)
        self.assertIn("How should retrieval work?", packet.messages[1].content)
        self.assertIn("Use SQLite first", packet.messages[0].content)
        self.assertTrue(packet.token_budget_report["dropped"])

    def test_orchestrator_runs_turn_commits_safe_memory_and_stages_uncertain_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
            store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="decision",
                    text="Use SQLite for local memory storage.",
                    source="user_stated",
                    confidence=0.95,
                    importance=0.9,
                )
            )
            safe = MemoryCandidate(
                record=MemoryRecord(
                    kind="fact",
                    text="The user is building a reusable conversation memory library.",
                    metadata={"key": "project"},
                    source="user_stated",
                    confidence=0.9,
                    importance=0.8,
                ),
                action=MemoryAction.COMMIT,
            )
            inferred = MemoryCandidate(
                record=MemoryRecord(
                    kind="preference",
                    text="The user might prefer very terse responses.",
                    source="assistant_inferred",
                    confidence=0.6,
                    importance=0.4,
                ),
                action=MemoryAction.COMMIT,
            )
            chat = StaticChatModel("Here is the next answer.")
            extractor = StaticMemoryExtractor(MemoryWriteSet([safe, inferred]))
            result = ConversationOrchestrator(store, chat, extractor).run_turn(
                "s1",
                "Continue the SQLite retrieval design.",
                user_id="u1",
                max_context_tokens=500,
            )

            self.assertEqual(result.assistant_text, "Here is the next answer.")
            self.assertEqual(len(result.committed_memories), 1)
            self.assertEqual(len(result.staged_memories), 1)
            self.assertEqual(store.get_session("s1", "u1").current_goal, "Continue the SQLite retrieval design.")
            self.assertEqual(len(store.recent_turns("s1", "u1")), 1)
            self.assertIn("SQLite", chat.calls[0][0].content)

    def test_conflicting_memory_is_staged_unless_marked_as_high_confidence_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
            old = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="preference",
                    text="The user prefers Pinecone for memory storage.",
                    metadata={"key": "memory_store"},
                    source="user_stated",
                    confidence=0.95,
                )
            )
            conflict = MemoryCandidate(
                record=MemoryRecord(
                    kind="preference",
                    text="The user prefers SQLite for memory storage.",
                    metadata={"key": "memory_store"},
                    source="user_stated",
                    confidence=0.95,
                ),
                action=MemoryAction.COMMIT,
            )
            orchestrator = ConversationOrchestrator(
                store,
                StaticChatModel(),
                StaticMemoryExtractor(MemoryWriteSet([conflict])),
            )
            result = orchestrator.run_turn("s1", "Use SQLite instead.", user_id="u1")
            self.assertEqual(len(result.committed_memories), 0)
            self.assertEqual(len(result.staged_memories), 1)
            self.assertFalse(store.get_memory(old.memory_id).stale)

            replacement = MemoryCandidate(
                record=MemoryRecord(
                    kind="preference",
                    text="The user prefers SQLite for memory storage.",
                    metadata={"key": "memory_store"},
                    source="user_stated",
                    confidence=0.95,
                ),
                action=MemoryAction.MARK_CONFLICT,
            )
            orchestrator = ConversationOrchestrator(
                store,
                StaticChatModel(),
                StaticMemoryExtractor(MemoryWriteSet([replacement])),
            )
            result = orchestrator.run_turn("s1", "Replace the old memory.", user_id="u1")
            self.assertEqual(len(result.committed_memories), 1)
            self.assertTrue(store.get_memory(old.memory_id).stale)

    def test_long_user_input_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3")
            store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="fact",
                    text="Only user one should retrieve this SQLite preference.",
                    source="user_stated",
                    confidence=0.9,
                )
            )
            chat = StaticChatModel("done")
            orchestrator = ConversationOrchestrator(store, chat, StaticMemoryExtractor())
            long_input = "SQLite retrieval " * 1000
            result = orchestrator.run_turn("s1", long_input, user_id="u2", max_context_tokens=80)

            self.assertLessEqual(total_message_tokens(result.context_packet.messages), 80)
            self.assertEqual(result.context_packet.retrieved_memories, [])
            self.assertEqual(store.recent_turns("s1", "u1"), [])
            self.assertEqual(len(store.recent_turns("s1", "u2")), 1)

    def test_rag_migration_chunks_and_embeddings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3", vector_enabled=False)
            memory = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="decision",
                    text="Use Ollama embeddinggemma with sqlite-vec for durable local RAG memory.",
                    source="user_stated",
                    confidence=0.95,
                    importance=0.9,
                )
            )
            chunks = store.list_chunks(memory.memory_id, user_id="u1")
            self.assertEqual(len(chunks), 1)
            self.assertIn("embeddinggemma", chunks[0].text)

            results = store.search_memory_results(
                "local RAG memory",
                user_id="u1",
                embedder=HashingEmbedder(),
            )
            self.assertEqual(results[0].memory.memory_id, memory.memory_id)
            self.assertIn("fts", results[0].sources)

    def test_sqlite_vec_hybrid_search_when_extension_is_available(self):
        try:
            import sqlite_vec  # noqa: F401
        except Exception:
            self.skipTest("sqlite-vec is not installed in this Python environment")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.sqlite3", vector_dimensions=16)
            memory = store.insert_memory(
                MemoryRecord(
                    user_id="u1",
                    kind="fact",
                    text="The durable memory skill uses a managed sqlite-vec dependency.",
                    source="user_stated",
                    confidence=0.95,
                    importance=0.8,
                )
            )
            embeddings = store.embed_memory(memory.memory_id, HashingEmbedder(), "test", "hashing")
            self.assertEqual(len(embeddings), 1)
            self.assertTrue(store.vector_available)

            results = store.search_memory_results(
                "managed sqlite-vec dependency",
                user_id="u1",
                embedder=HashingEmbedder(),
                provider="test",
                model="hashing",
            )
            self.assertEqual(results[0].memory.memory_id, memory.memory_id)
            self.assertIn("vector", results[0].sources)

    def test_ollama_embedder_parses_mocked_embedding_response(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"embeddings": [[0.1, 0.2], [3, 4]]}'

        with patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            vectors = OllamaEmbedder(model="embeddinggemma", timeout=1).embed_batch(["alpha", "beta"])

        self.assertEqual(vectors, [[0.1, 0.2], [3.0, 4.0]])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/embed")
        self.assertIn(b'"model": "embeddinggemma"', request.data)


if __name__ == "__main__":
    unittest.main()
