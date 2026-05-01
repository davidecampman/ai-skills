from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from .models import (
    CandidateStatus,
    EmbeddingRecord,
    MemoryAction,
    MemoryCandidate,
    MemoryChunk,
    MemoryRecord,
    MemorySearchResult,
    SessionState,
    TurnRecord,
    now_iso,
    to_dict,
)


DEFAULT_CHUNK_CHARS = 1200
DEFAULT_CHUNK_OVERLAP = 120


class SQLiteMemoryStore:
    def __init__(
        self,
        path: Path | str,
        vector_enabled: bool = True,
        vector_dimensions: int | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.vector_available = False
        self.vector_error = ""
        self.vector_dimensions = vector_dimensions
        self.migrate(vector_enabled=vector_enabled, vector_dimensions=vector_dimensions)

    def migrate(self, vector_enabled: bool = True, vector_dimensions: int | None = None) -> None:
        self.conn.executescript(
            """
            create table if not exists sessions (
              session_id text not null,
              user_id text not null,
              payload text not null,
              updated_at text not null,
              primary key (session_id, user_id)
            );
            create table if not exists turns (
              id integer primary key autoincrement,
              session_id text not null,
              user_id text not null,
              user_message text not null,
              assistant_message text not null,
              context_summary text not null,
              created_at text not null
            );
            create table if not exists memories (
              id text primary key,
              user_id text not null,
              kind text not null,
              text text not null,
              metadata text not null,
              source text not null,
              confidence real not null,
              importance real not null,
              created_at text not null,
              updated_at text not null,
              stale integer not null default 0
            );
            create table if not exists memory_candidates (
              id text primary key,
              user_id text not null,
              action text not null,
              status text not null,
              payload text not null,
              conflict_memory_ids text not null,
              reason text not null,
              created_at text not null
            );
            create table if not exists memory_chunks (
              id text primary key,
              memory_id text not null,
              user_id text not null,
              chunk_index integer not null,
              text text not null,
              metadata text not null,
              stale integer not null default 0,
              created_at text not null,
              unique(memory_id, chunk_index)
            );
            create table if not exists memory_embeddings (
              id integer primary key autoincrement,
              chunk_id text not null,
              memory_id text not null,
              user_id text not null,
              provider text not null,
              model text not null,
              dimensions integer not null,
              embedding_json text not null,
              created_at text not null,
              updated_at text not null,
              unique(chunk_id, provider, model)
            );
            """
        )
        self.conn.execute(
            """
            create virtual table if not exists memory_fts using fts5(
              memory_id unindexed,
              user_id unindexed,
              kind,
              text,
              metadata
            )
            """
        )
        self.conn.execute(
            """
            create virtual table if not exists memory_chunk_fts using fts5(
              chunk_id unindexed,
              memory_id unindexed,
              user_id unindexed,
              kind,
              text,
              metadata
            )
            """
        )
        if vector_enabled and vector_dimensions:
            self.ensure_vector_table(vector_dimensions)
        elif vector_enabled and self._vector_table_exists():
            self._load_vector_extension()
        self.conn.commit()

    def _load_vector_extension(self) -> bool:
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
        except Exception as exc:  # noqa: BLE001 - keep degraded FTS mode usable.
            self.vector_available = False
            self.vector_error = str(exc)
            try:
                self.conn.enable_load_extension(False)
            except Exception:
                pass
            return False
        self.vector_available = True
        self.vector_error = ""
        return True

    def ensure_vector_table(self, dimensions: int) -> bool:
        dimensions = int(dimensions)
        if not self._load_vector_extension():
            return False
        try:
            self.conn.execute(
                f"create virtual table if not exists memory_embedding_vec using vec0(embedding float[{dimensions}])"
            )
        except Exception as exc:  # noqa: BLE001 - keep degraded FTS mode usable.
            self.vector_available = False
            self.vector_error = str(exc)
            return False
        self.vector_dimensions = dimensions
        return True

    def get_session(self, session_id: str, user_id: str = "default") -> SessionState:
        row = self.conn.execute(
            "select payload from sessions where session_id = ? and user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if not row:
            return SessionState(session_id=session_id, user_id=user_id)
        data = json.loads(row["payload"])
        return SessionState(**data)

    def save_session(self, state: SessionState) -> None:
        state.updated_at = now_iso()
        self.conn.execute(
            """
            insert into sessions (session_id, user_id, payload, updated_at)
            values (?, ?, ?, ?)
            on conflict(session_id, user_id) do update set
              payload=excluded.payload,
              updated_at=excluded.updated_at
            """,
            (state.session_id, state.user_id, json.dumps(to_dict(state), sort_keys=True), state.updated_at),
        )
        self.conn.commit()

    def add_turn(self, turn: TurnRecord) -> int:
        created_at = turn.created_at or now_iso()
        cursor = self.conn.execute(
            """
            insert into turns (session_id, user_id, user_message, assistant_message, context_summary, created_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                turn.session_id,
                turn.user_id,
                turn.user_message,
                turn.assistant_message,
                turn.context_summary,
                created_at,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def recent_turns(self, session_id: str, user_id: str = "default", limit: int = 6) -> list[TurnRecord]:
        rows = self.conn.execute(
            """
            select id, session_id, user_id, user_message, assistant_message, context_summary, created_at
            from turns
            where session_id = ? and user_id = ?
            order by id desc
            limit ?
            """,
            (session_id, user_id, limit),
        ).fetchall()
        turns = [
            TurnRecord(
                session_id=row["session_id"],
                user_id=row["user_id"],
                user_message=row["user_message"],
                assistant_message=row["assistant_message"],
                context_summary=row["context_summary"],
                created_at=row["created_at"],
                turn_id=row["id"],
            )
            for row in rows
        ]
        return list(reversed(turns))

    def insert_memory(self, record: MemoryRecord) -> MemoryRecord:
        if not record.memory_id:
            record.memory_id = uuid4().hex
        if not record.created_at:
            record.created_at = now_iso()
        record.updated_at = now_iso()
        metadata_json = json.dumps(record.metadata, sort_keys=True)
        self.conn.execute(
            """
            insert into memories
              (id, user_id, kind, text, metadata, source, confidence, importance, created_at, updated_at, stale)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              user_id=excluded.user_id,
              kind=excluded.kind,
              text=excluded.text,
              metadata=excluded.metadata,
              source=excluded.source,
              confidence=excluded.confidence,
              importance=excluded.importance,
              updated_at=excluded.updated_at,
              stale=excluded.stale
            """,
            (
                record.memory_id,
                record.user_id,
                record.kind,
                record.text,
                metadata_json,
                record.source,
                record.confidence,
                record.importance,
                record.created_at,
                record.updated_at,
                int(record.stale),
            ),
        )
        self.conn.execute("delete from memory_fts where memory_id = ?", (record.memory_id,))
        self.conn.execute(
            "insert into memory_fts (memory_id, user_id, kind, text, metadata) values (?, ?, ?, ?, ?)",
            (record.memory_id, record.user_id, record.kind, record.text, metadata_json),
        )
        self._replace_chunks(record)
        self.conn.commit()
        return record

    def _replace_chunks(self, record: MemoryRecord) -> None:
        assert record.memory_id is not None
        self.conn.execute("delete from memory_chunk_fts where memory_id = ?", (record.memory_id,))
        old_embeddings = self.conn.execute(
            "select id from memory_embeddings where memory_id = ?",
            (record.memory_id,),
        ).fetchall()
        if old_embeddings and self._vector_table_exists() and self._load_vector_extension():
            for row in old_embeddings:
                self.conn.execute("delete from memory_embedding_vec where rowid = ?", (row["id"],))
        self.conn.execute("delete from memory_embeddings where memory_id = ?", (record.memory_id,))
        self.conn.execute("delete from memory_chunks where memory_id = ?", (record.memory_id,))

        chunks = chunk_text(record.text)
        for index, text in enumerate(chunks):
            chunk = MemoryChunk(
                chunk_id=uuid4().hex,
                memory_id=record.memory_id,
                user_id=record.user_id,
                text=text,
                chunk_index=index,
                metadata={"memory_kind": record.kind, **record.metadata},
                stale=record.stale,
            )
            self.insert_chunk(chunk, kind=record.kind, commit=False)

    def insert_chunk(self, chunk: MemoryChunk, kind: str = "memory", commit: bool = True) -> MemoryChunk:
        if not chunk.chunk_id:
            chunk.chunk_id = uuid4().hex
        metadata_json = json.dumps(chunk.metadata, sort_keys=True)
        self.conn.execute(
            """
            insert into memory_chunks (id, memory_id, user_id, chunk_index, text, metadata, stale, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(memory_id, chunk_index) do update set
              id=excluded.id,
              user_id=excluded.user_id,
              text=excluded.text,
              metadata=excluded.metadata,
              stale=excluded.stale
            """,
            (
                chunk.chunk_id,
                chunk.memory_id,
                chunk.user_id,
                chunk.chunk_index,
                chunk.text,
                metadata_json,
                int(chunk.stale),
                chunk.created_at,
            ),
        )
        self.conn.execute(
            """
            insert into memory_chunk_fts (chunk_id, memory_id, user_id, kind, text, metadata)
            values (?, ?, ?, ?, ?, ?)
            """,
            (chunk.chunk_id, chunk.memory_id, chunk.user_id, kind, chunk.text, metadata_json),
        )
        if commit:
            self.conn.commit()
        return chunk

    def embed_memory(self, memory_id: str, embedder, provider: str, model: str) -> list[EmbeddingRecord]:
        memory = self.get_memory(memory_id)
        if not memory:
            raise KeyError(f"Unknown memory id: {memory_id}")
        chunks = self.list_chunks(memory_id=memory_id, user_id=memory.user_id, include_stale=False)
        records: list[EmbeddingRecord] = []
        for chunk in chunks:
            vector = embedder.embed(chunk.text)
            if not vector:
                continue
            if self.vector_dimensions and len(vector) != self.vector_dimensions:
                raise ValueError(
                    f"Embedding dimensions changed from {self.vector_dimensions} to {len(vector)} for {model}."
                )
            if not self.vector_dimensions:
                self.ensure_vector_table(len(vector))
            records.append(
                self.insert_embedding(
                    EmbeddingRecord(
                        chunk_id=chunk.chunk_id or "",
                        memory_id=chunk.memory_id,
                        user_id=chunk.user_id,
                        provider=provider,
                        model=model,
                        dimensions=len(vector),
                        embedding=vector,
                    )
                )
            )
        return records

    def insert_embedding(self, record: EmbeddingRecord) -> EmbeddingRecord:
        now = now_iso()
        cursor = self.conn.execute(
            """
            insert into memory_embeddings
              (chunk_id, memory_id, user_id, provider, model, dimensions, embedding_json, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(chunk_id, provider, model) do update set
              memory_id=excluded.memory_id,
              user_id=excluded.user_id,
              dimensions=excluded.dimensions,
              embedding_json=excluded.embedding_json,
              updated_at=excluded.updated_at
            returning id
            """,
            (
                record.chunk_id,
                record.memory_id,
                record.user_id,
                record.provider,
                record.model,
                record.dimensions,
                json.dumps(record.embedding),
                record.created_at or now,
                now,
            ),
        )
        row = cursor.fetchone()
        record.embedding_id = int(row["id"])
        record.updated_at = now
        if self.ensure_vector_table(record.dimensions):
            import sqlite_vec

            self.conn.execute(
                "delete from memory_embedding_vec where rowid = ?",
                (record.embedding_id,),
            )
            self.conn.execute(
                "insert into memory_embedding_vec(rowid, embedding) values (?, ?)",
                (record.embedding_id, sqlite_vec.serialize_float32(record.embedding)),
            )
        self.conn.commit()
        return record

    def list_chunks(
        self,
        memory_id: str | None = None,
        user_id: str = "default",
        include_stale: bool = False,
    ) -> list[MemoryChunk]:
        clauses = ["user_id = ?"]
        params: list[object] = [user_id]
        if memory_id:
            clauses.append("memory_id = ?")
            params.append(memory_id)
        if not include_stale:
            clauses.append("stale = 0")
        rows = self.conn.execute(
            f"""
            select * from memory_chunks
            where {' and '.join(clauses)}
            order by memory_id, chunk_index
            """,
            params,
        ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        row = self.conn.execute("select * from memories where id = ?", (memory_id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def list_memories(self, user_id: str = "default", include_stale: bool = False) -> list[MemoryRecord]:
        stale_clause = "" if include_stale else "and stale = 0"
        rows = self.conn.execute(
            f"select * from memories where user_id = ? {stale_clause} order by created_at desc",
            (user_id,),
        ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def search_memories(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 8,
        include_stale: bool = False,
    ) -> list[MemoryRecord]:
        return [
            result.memory
            for result in self.search_memory_results(
                query,
                user_id=user_id,
                limit=limit,
                include_stale=include_stale,
            )
        ]

    def search_memory_results(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 8,
        include_stale: bool = False,
        embedder=None,
        provider: str = "ollama",
        model: str = "embeddinggemma",
    ) -> list[MemorySearchResult]:
        scored: dict[str, MemorySearchResult] = {}
        for memory, score, chunk_id in self._fts_records(query, user_id, include_stale):
            scored[memory.memory_id or ""] = MemorySearchResult(
                memory=memory,
                score=score,
                lexical_score=score,
                sources=["fts"],
                chunk_id=chunk_id,
            )

        if embedder is not None and self.vector_available:
            try:
                vector = embedder.embed(query)
                for memory, vector_score, chunk_id in self._vector_records(
                    vector,
                    user_id=user_id,
                    provider=provider,
                    model=model,
                    limit=max(limit * 2, 12),
                    include_stale=include_stale,
                ):
                    key = memory.memory_id or ""
                    existing = scored.get(key)
                    if existing:
                        existing.vector_score = max(existing.vector_score, vector_score)
                        existing.score += vector_score
                        if "vector" not in existing.sources:
                            existing.sources.append("vector")
                    else:
                        scored[key] = MemorySearchResult(
                            memory=memory,
                            score=vector_score,
                            vector_score=vector_score,
                            sources=["vector"],
                            chunk_id=chunk_id,
                        )
            except Exception:
                pass

        if not scored:
            records = self.list_memories(user_id=user_id, include_stale=include_stale)
            for record in records:
                scored[record.memory_id or ""] = MemorySearchResult(
                    memory=record,
                    score=self._rank_record(record, query),
                    lexical_score=0.0,
                    sources=["fallback"],
                )

        for result in scored.values():
            result.score += self._rank_record(result.memory, query)

        ranked = sorted(scored.values(), key=lambda result: result.score, reverse=True)
        return ranked[:limit]

    def find_conflicts(self, record: MemoryRecord) -> list[MemoryRecord]:
        key = record.metadata.get("key")
        if not key:
            return []
        rows = self.conn.execute(
            """
            select * from memories
            where user_id = ? and kind = ? and stale = 0
            """,
            (record.user_id, record.kind),
        ).fetchall()
        conflicts = []
        for row in rows:
            existing = self._row_to_memory(row)
            if existing.memory_id == record.memory_id:
                continue
            if existing.metadata.get("key") == key and existing.text.strip() != record.text.strip():
                conflicts.append(existing)
        return conflicts

    def mark_stale(self, memory_id: str) -> None:
        self.conn.execute(
            "update memories set stale = 1, updated_at = ? where id = ?",
            (now_iso(), memory_id),
        )
        self.conn.execute("update memory_chunks set stale = 1 where memory_id = ?", (memory_id,))
        self.conn.commit()

    def stage_candidate(
        self,
        candidate: MemoryCandidate,
        status: CandidateStatus | str = CandidateStatus.STAGED,
    ) -> MemoryCandidate:
        if not candidate.candidate_id:
            candidate.candidate_id = uuid4().hex
        action = MemoryAction(candidate.action).value
        self.conn.execute(
            """
            insert into memory_candidates
              (id, user_id, action, status, payload, conflict_memory_ids, reason, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              action=excluded.action,
              status=excluded.status,
              payload=excluded.payload,
              conflict_memory_ids=excluded.conflict_memory_ids,
              reason=excluded.reason
            """,
            (
                candidate.candidate_id,
                candidate.record.user_id,
                action,
                CandidateStatus(status).value,
                json.dumps(to_dict(candidate.record), sort_keys=True),
                json.dumps(candidate.conflict_memory_ids),
                candidate.reason,
                now_iso(),
            ),
        )
        self.conn.commit()
        return candidate

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        row = self.conn.execute(
            "select id, action, payload, conflict_memory_ids, reason from memory_candidates where id = ?",
            (candidate_id,),
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    def set_candidate_status(self, candidate_id: str, status: CandidateStatus | str) -> None:
        self.conn.execute(
            "update memory_candidates set status = ? where id = ?",
            (CandidateStatus(status).value, candidate_id),
        )
        self.conn.commit()

    def list_candidates(
        self,
        user_id: str = "default",
        status: CandidateStatus | str = CandidateStatus.STAGED,
    ) -> list[MemoryCandidate]:
        rows = self.conn.execute(
            """
            select id, action, payload, conflict_memory_ids, reason
            from memory_candidates
            where user_id = ? and status = ?
            order by created_at desc
            """,
            (user_id, CandidateStatus(status).value),
        ).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def close(self) -> None:
        self.conn.close()

    def _fts_records(
        self,
        query: str,
        user_id: str,
        include_stale: bool,
    ) -> list[tuple[MemoryRecord, float, str | None]]:
        terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
        if not terms:
            return []
        fts_query = " OR ".join(f"{term}*" for term in terms[:12])
        try:
            rows = self.conn.execute(
                """
                select m.*, c.id as chunk_id, bm25(memory_chunk_fts) as rank
                from memory_chunk_fts f
                join memory_chunks c on c.id = f.chunk_id
                join memories m on m.id = c.memory_id
                where memory_chunk_fts match ? and m.user_id = ? and (? or m.stale = 0)
                """,
                (fts_query, user_id, int(include_stale)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        results = []
        for row in rows:
            rank = float(row["rank"] or 0.0)
            score = 1.0 / (1.0 + abs(rank))
            results.append((self._row_to_memory(row), score, row["chunk_id"]))
        return results

    def _vector_records(
        self,
        vector: list[float],
        user_id: str,
        provider: str,
        model: str,
        limit: int,
        include_stale: bool,
    ) -> list[tuple[MemoryRecord, float, str | None]]:
        if not vector or not self._vector_table_exists():
            return []
        try:
            import sqlite_vec

            rows = self.conn.execute(
                """
                select m.*, c.id as chunk_id, v.distance
                from memory_embedding_vec v
                join memory_embeddings e on e.id = v.rowid
                join memory_chunks c on c.id = e.chunk_id
                join memories m on m.id = e.memory_id
                where v.embedding match ?
                  and v.k = ?
                  and e.user_id = ?
                  and e.provider = ?
                  and e.model = ?
                  and (? or m.stale = 0)
                order by v.distance
                limit ?
                """,
                (
                    sqlite_vec.serialize_float32(vector),
                    limit,
                    user_id,
                    provider,
                    model,
                    int(include_stale),
                    limit,
                ),
            ).fetchall()
        except Exception:
            return []
        results = []
        for row in rows:
            distance = float(row["distance"] or 0.0)
            score = 1.0 / (1.0 + max(distance, 0.0))
            results.append((self._row_to_memory(row), score, row["chunk_id"]))
        return results

    def _rank_record(self, record: MemoryRecord, query: str) -> float:
        query_terms = set(re.findall(r"[A-Za-z0-9_]+", query.lower()))
        record_terms = set(re.findall(r"[A-Za-z0-9_]+", f"{record.kind} {record.text}".lower()))
        overlap = len(query_terms & record_terms)
        kind_boost = {
            "preference": 0.25,
            "decision": 0.2,
            "fact": 0.15,
            "profile": 0.15,
            "artifact": 0.05,
            "episode": 0.0,
        }.get(record.kind, 0.0)
        stale_penalty = -2.0 if record.stale else 0.0
        return overlap + record.importance + record.confidence + kind_boost + stale_penalty

    def _vector_table_exists(self) -> bool:
        row = self.conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'memory_embedding_vec'"
        ).fetchone()
        return bool(row)

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["id"],
            user_id=row["user_id"],
            kind=row["kind"],
            text=row["text"],
            metadata=json.loads(row["metadata"]),
            source=row["source"],
            confidence=float(row["confidence"]),
            importance=float(row["importance"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            stale=bool(row["stale"]),
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> MemoryChunk:
        return MemoryChunk(
            chunk_id=row["id"],
            memory_id=row["memory_id"],
            user_id=row["user_id"],
            chunk_index=int(row["chunk_index"]),
            text=row["text"],
            metadata=json.loads(row["metadata"]),
            stale=bool(row["stale"]),
            created_at=row["created_at"],
        )

    def _row_to_candidate(self, row: sqlite3.Row) -> MemoryCandidate:
        return MemoryCandidate(
            record=MemoryRecord(**json.loads(row["payload"])),
            action=MemoryAction(row["action"]),
            conflict_memory_ids=json.loads(row["conflict_memory_ids"]),
            reason=row["reason"],
            candidate_id=row["id"],
        )


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    compact = " ".join(text.split())
    if not compact:
        return []
    if len(compact) <= max_chars:
        return [compact]

    chunks: list[str] = []
    start = 0
    while start < len(compact):
        end = min(len(compact), start + max_chars)
        if end < len(compact):
            boundary = max(compact.rfind(". ", start, end), compact.rfind(" ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunks.append(compact[start:end].strip())
        if end >= len(compact):
            break
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
