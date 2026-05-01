---
name: contextual-memory
description: Build, install, operate, or integrate durable local RAG memory for Codex and other LLM workflows. Use when Codex needs persistent conversation memory, SQLite/FTS5 retrieval, sqlite-vec vector search, Ollama embeddinggemma embeddings, context packing, staged memory writeback, conflict/staleness handling, or the bundled contextual_memory Python library and manage_memory.py CLI.
---

# Contextual Memory

Use this skill for durable local memory with bounded model context. Treat the
LLM context window as working memory; store durable state in SQLite and inject
only compact, relevant context packets.

## Quick Start

Use the bundled CLI from the repo copy:

```bash
python3 contextual-memory/scripts/manage_memory.py doctor --fix
python3 contextual-memory/scripts/manage_memory.py init
```

Use the installed skill copy:

```bash
python3 ~/.codex/skills/contextual-memory/scripts/manage_memory.py doctor --fix
python3 ~/.codex/skills/contextual-memory/scripts/manage_memory.py init
```

Default database:

```text
~/.codex/memory/contextual_memory.sqlite3
```

## Managed Dependencies

The CLI auto-installs missing runtime dependencies for commands that need them.
It only writes to these locations:

- `~/.codex/memory/contextual-memory-venv`
- `~/.codex/memory/contextual_memory.sqlite3`
- the Ollama model cache via `ollama pull embeddinggemma`
- Homebrew's Ollama install only when Ollama is missing on macOS and `brew` exists

Do not use Codex OAuth for embeddings. OpenAI embeddings are intentionally not
enabled by default. The default embedding backend is local Ollama
`embeddinggemma`; the default vector engine is `sqlite-vec`.

## Common Commands

```bash
python3 contextual-memory/scripts/manage_memory.py doctor --fix
```

```bash
python3 contextual-memory/scripts/manage_memory.py remember \
  --kind preference \
  --source user_stated \
  --text "The user prefers local-first memory with Ollama and sqlite-vec."
```

```bash
python3 contextual-memory/scripts/manage_memory.py search \
  --query "local memory retrieval" \
  --format table
```

```bash
python3 contextual-memory/scripts/manage_memory.py context \
  --session-id main \
  --query "What memory design did we choose?"
```

```bash
python3 contextual-memory/scripts/manage_memory.py candidates list
```

## Design Rules

- SQLite + FTS5 is the durable baseline and must keep working if vector setup degrades.
- `sqlite-vec` is preferred for vector search and installed in the managed venv.
- Ollama `embeddinggemma` is the first real embedding backend.
- Auto-commit only `user_stated` or `tool_verified` memories with confidence `>= 0.75`.
- Stage assistant-inferred, low-confidence, ambiguous, or conflicting memories.
- Exclude stale memories from retrieval by default.
- Preserve provenance in context packets so callers can audit what was sent.

Read [REFERENCE.md](REFERENCE.md) before changing schemas, dependency behavior,
embedding providers, or writeback policy.
