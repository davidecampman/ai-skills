#!/usr/bin/env python3
"""
RAG Memory Backend — Python (ChromaDB + sentence-transformers)

Cross-platform persistent memory with semantic vector search.
Falls back to SQLite FTS5 if ChromaDB/sentence-transformers are unavailable.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "backend": "auto",
    "storage_path": ".claude/memory",
    "global_storage_path": os.path.join(str(Path.home()), ".claude", "memory"),
    "embedding_model": "all-MiniLM-L6-v2",
    "max_results": 10,
    "similarity_threshold": 0.3,
    "auto_tag": True,
    "default_scope": "project",
}

AUTO_TAG_RULES = {
    "architecture": ["database", "schema", "api", "design", "pattern", "structure", "microservice", "monolith"],
    "preference": ["prefer", "like", "always", "never", "style", "convention"],
    "bug": ["bug", "fix", "issue", "error", "crash", "workaround", "patch"],
    "decision": ["decided", "chose", "because", "rationale", "trade-off", "tradeoff"],
    "config": ["config", "environment", "setup", "install", "deploy", "ci/cd"],
    "api": ["endpoint", "request", "response", "rest", "graphql", "webhook"],
    "security": ["auth", "token", "secret", "permission", "cors", "csrf"],
    "performance": ["slow", "fast", "optimize", "cache", "latency", "throughput"],
}


def load_config(storage_path: str) -> dict:
    config_file = os.path.join(storage_path, "config.json")
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            config.update(json.load(f))
    return config


def get_storage_path(scope: str, config: dict) -> str:
    if scope == "global":
        return os.path.expanduser(config["global_storage_path"])
    return config["storage_path"]


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------

def auto_detect_tags(content: str) -> list[str]:
    content_lower = content.lower()
    tags = []
    for tag, keywords in AUTO_TAG_RULES.items():
        if any(kw in content_lower for kw in keywords):
            tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# SQLite Backend (shared metadata + FTS fallback)
# ---------------------------------------------------------------------------

class SQLiteBackend:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                source TEXT DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                tags,
                content=memories,
                content_rowid=rowid
            );

            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES('delete', old.rowid, old.content, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES('delete', old.rowid, old.content, old.tags);
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END;
        """)

    def store(self, content: str, tags: str = "", source: str = "user") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        memory_id = str(uuid.uuid4())[:8]
        self.conn.execute(
            "INSERT INTO memories (id, content, tags, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (memory_id, content, tags, source, now, now),
        )
        self.conn.commit()
        return {"id": memory_id, "content": content, "tags": tags, "created_at": now}

    def recall_fts(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search fallback."""
        # Try FTS first, fall back to LIKE
        try:
            fts_query = " OR ".join(query.split())
            rows = self.conn.execute(
                """SELECT m.* FROM memories m
                   JOIN memories_fts fts ON m.rowid = fts.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            pass

        # LIKE fallback
        like_pattern = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE content LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (like_pattern, like_pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, query: str) -> int:
        # Try by ID first
        deleted = self.conn.execute("DELETE FROM memories WHERE id = ?", (query,)).rowcount
        if deleted:
            self.conn.commit()
            return deleted
        # Then by content match
        deleted = self.conn.execute(
            "DELETE FROM memories WHERE content LIKE ?", (f"%{query}%",)
        ).rowcount
        self.conn.commit()
        return deleted

    def list_all(self, tags_filter: str = "") -> list[dict]:
        if tags_filter:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE tags LIKE ? ORDER BY updated_at DESC",
                (f"%{tags_filter}%",),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM memories ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# ChromaDB Backend (vector search)
# ---------------------------------------------------------------------------

class ChromaBackend:
    def __init__(self, chroma_path: str, model_name: str):
        try:
            import chromadb

            self.client = chromadb.PersistentClient(path=chroma_path)
            self.collection = self.client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
            self.available = True
        except ImportError:
            self.available = False

    def store(self, memory_id: str, content: str, tags: str):
        if not self.available:
            return
        self.collection.upsert(
            ids=[memory_id],
            documents=[content],
            metadatas=[{"tags": tags}],
        )

    def recall(self, query: str, limit: int = 10, threshold: float = 0.3) -> list[str]:
        if not self.available:
            return []
        results = self.collection.query(query_texts=[query], n_results=limit)
        # Return IDs sorted by relevance
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [
            mid for mid, dist in zip(ids, distances) if (1 - dist) >= threshold
        ]

    def forget(self, memory_id: str):
        if not self.available:
            return
        try:
            self.collection.delete(ids=[memory_id])
        except Exception:
            pass

    def count(self) -> int:
        if not self.available:
            return 0
        return self.collection.count()


# ---------------------------------------------------------------------------
# Unified Memory Manager
# ---------------------------------------------------------------------------

class MemoryManager:
    def __init__(self, scope: str = "project"):
        project_config_path = DEFAULT_CONFIG["storage_path"]
        self.config = load_config(project_config_path)
        self.scope = scope
        storage = get_storage_path(scope, self.config)
        os.makedirs(storage, exist_ok=True)

        db_path = os.path.join(storage, "memories.db")
        self.sqlite = SQLiteBackend(db_path)

        chroma_path = os.path.join(storage, "chroma")
        self.chroma = ChromaBackend(chroma_path, self.config["embedding_model"])

    @property
    def has_vector_search(self) -> bool:
        return self.chroma.available

    def remember(self, content: str, tags: str = "", source: str = "user") -> dict:
        if not tags and self.config.get("auto_tag", True):
            detected = auto_detect_tags(content)
            tags = ",".join(detected)

        result = self.sqlite.store(content, tags, source)
        self.chroma.store(result["id"], content, tags)
        return result

    def recall(self, query: str, limit: int = 0) -> list[dict]:
        if not limit:
            limit = self.config.get("max_results", 10)
        threshold = self.config.get("similarity_threshold", 0.3)

        # Try vector search first
        if self.has_vector_search:
            ranked_ids = self.chroma.recall(query, limit, threshold)
            if ranked_ids:
                results = []
                for mid in ranked_ids:
                    rows = self.sqlite.conn.execute(
                        "SELECT * FROM memories WHERE id = ?", (mid,)
                    ).fetchall()
                    results.extend(dict(r) for r in rows)
                if results:
                    return results[:limit]

        # Fall back to FTS
        return self.sqlite.recall_fts(query, limit)

    def forget(self, query: str) -> int:
        # Get memories to delete (for chroma cleanup)
        like = f"%{query}%"
        rows = self.sqlite.conn.execute(
            "SELECT id FROM memories WHERE id = ? OR content LIKE ?", (query, like)
        ).fetchall()
        for row in rows:
            self.chroma.forget(row["id"])
        return self.sqlite.forget(query)

    def list_all(self, tags_filter: str = "") -> list[dict]:
        return self.sqlite.list_all(tags_filter)

    def status(self) -> dict:
        return {
            "scope": self.scope,
            "backend": "chromadb (vector)" if self.has_vector_search else "sqlite (fts5)",
            "total_memories": self.sqlite.count(),
            "vector_entries": self.chroma.count() if self.has_vector_search else "n/a",
            "storage_path": get_storage_path(self.scope, self.config),
            "embedding_model": self.config["embedding_model"] if self.has_vector_search else "n/a",
        }

    def close(self):
        self.sqlite.close()


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def setup():
    """Install Python dependencies for full vector search."""
    import subprocess

    deps = ["chromadb", "sentence-transformers"]
    print(f"Installing dependencies: {', '.join(deps)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
    print("Setup complete. Vector search is now available.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_memory(mem: dict, index: int = 0) -> str:
    date = mem["created_at"][:10]
    tags = f" (tags: {mem['tags']})" if mem.get("tags") else ""
    prefix = f"{index}. " if index else ""
    return f"{prefix}[{date}]{tags}\n   {mem['content']}"


def main():
    parser = argparse.ArgumentParser(description="RAG Memory Manager")
    parser.add_argument("command", choices=["remember", "recall", "forget", "list", "status", "setup"])
    parser.add_argument("content", nargs="*", default=[], help="Content or query text")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--scope", default="project", choices=["project", "global"])
    parser.add_argument("--limit", type=int, default=0, help="Max results")
    parser.add_argument("--source", default="user", help="Memory source")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    text = " ".join(args.content)

    if args.command == "setup":
        setup()
        return

    mgr = MemoryManager(scope=args.scope)

    try:
        if args.command == "remember":
            if not text:
                print("Error: No content provided to remember.", file=sys.stderr)
                sys.exit(1)
            result = mgr.remember(text, tags=args.tags, source=args.source)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Stored memory (id: {result['id']})")
                print(f"Tags: [{result['tags']}]" if result["tags"] else "Tags: [none]")
                print(f"Content: {result['content'][:200]}")

        elif args.command == "recall":
            if not text:
                print("Error: No query provided.", file=sys.stderr)
                sys.exit(1)
            results = mgr.recall(text, limit=args.limit)
            if args.json:
                print(json.dumps(results, indent=2))
            elif results:
                print(f'Found {len(results)} memories matching "{text}":\n')
                for i, mem in enumerate(results, 1):
                    print(format_memory(mem, i))
                    print()
            else:
                print(f'No memories found matching "{text}".')

        elif args.command == "forget":
            if not text:
                print("Error: No query provided.", file=sys.stderr)
                sys.exit(1)
            deleted = mgr.forget(text)
            print(f"Deleted {deleted} memory(ies) matching \"{text}\".")

        elif args.command == "list":
            memories = mgr.list_all(tags_filter=args.tags)
            if args.json:
                print(json.dumps(memories, indent=2))
            elif memories:
                print(f"Total memories: {len(memories)}\n")
                for i, mem in enumerate(memories, 1):
                    print(format_memory(mem, i))
                    print()
            else:
                print("No memories stored yet.")

        elif args.command == "status":
            info = mgr.status()
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                print("Memory Store Status")
                print("=" * 40)
                for k, v in info.items():
                    print(f"  {k}: {v}")

    finally:
        mgr.close()


if __name__ == "__main__":
    main()
