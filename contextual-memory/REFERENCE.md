# Contextual Memory Reference

## Runtime Layout

- Skill source: `contextual-memory/`
- CLI: `scripts/manage_memory.py`
- Python package: `scripts/contextual_memory/`
- Managed venv: `~/.codex/memory/contextual-memory-venv`
- Default database: `~/.codex/memory/contextual_memory.sqlite3`
- Default embedding backend: Ollama `embeddinggemma`
- Default vector engine: `sqlite-vec==0.1.9`

## Storage Model

The database is local SQLite and keeps these durable records:

- `sessions`: rolling per-session state.
- `turns`: recent conversation turns.
- `memories`: canonical committed memories.
- `memory_candidates`: staged, committed, or rejected memory candidates.
- `memory_fts`: FTS5 index for canonical memories.
- `memory_chunks`: chunked memory text for longer artifacts.
- `memory_chunk_fts`: FTS5 index for chunks.
- `memory_embeddings`: provider/model/dimension metadata plus JSON vectors.
- `memory_embedding_vec`: optional sqlite-vec virtual table for vector lookup.

If `sqlite-vec` fails to load, keep the SQLite and FTS5 path operational and
report degraded vector mode.

## Dependency Policy

`manage_memory.py` may auto-install only the approved local dependencies:

- create or reuse `~/.codex/memory/contextual-memory-venv`
- install `sqlite-vec==0.1.9` into that venv
- use existing `ollama` when present
- run `brew install ollama` on macOS only when Ollama is missing and Homebrew exists
- run `ollama pull embeddinggemma` when the model is missing

Never install Python packages into the global interpreter. Never use Codex OAuth
for embeddings. OpenAI embeddings require a future explicit API-key adapter.

## Memory Write Policy

Auto-commit is allowed only when all are true:

- source is `user_stated` or `tool_verified`
- confidence is at least `0.75`
- no active conflict is found for the same user, kind, and metadata key

Stage instead of committing when source is assistant-inferred, confidence is
low, or an existing memory conflicts. Mark old memories stale only through an
explicit high-confidence replacement path.

## Retrieval Policy

Use hybrid retrieval when available:

- FTS5 chunk search for lexical recall.
- sqlite-vec nearest-neighbor search for semantic recall.
- Rank by lexical score, vector score, confidence, importance, kind boost, and
stale penalty.

If vector retrieval is unavailable, use FTS5 plus deterministic record ranking.
Search should exclude stale memories unless explicitly requested by library code.

## CLI Summary

```bash
python3 contextual-memory/scripts/manage_memory.py doctor --fix
python3 contextual-memory/scripts/manage_memory.py init
python3 contextual-memory/scripts/manage_memory.py remember --text "..." --kind fact --source user_stated
python3 contextual-memory/scripts/manage_memory.py search --query "..." --format json
python3 contextual-memory/scripts/manage_memory.py context --session-id main --query "..."
python3 contextual-memory/scripts/manage_memory.py embed backfill
python3 contextual-memory/scripts/manage_memory.py candidates list
```

All commands accept `--db` when a project or test needs an isolated database.
