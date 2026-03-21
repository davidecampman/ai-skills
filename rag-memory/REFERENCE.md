# RAG Memory — Reference

## Configuration

Configuration lives in `.claude/memory/config.json`:

```json
{
  "backend": "auto",
  "storage_path": ".claude/memory",
  "global_storage_path": "~/.claude/memory",
  "embedding_model": "all-MiniLM-L6-v2",
  "max_results": 10,
  "similarity_threshold": 0.3,
  "auto_tag": true,
  "default_scope": "project"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `backend` | `auto`, `python`, or `powershell` | `auto` |
| `storage_path` | Project-level memory path | `.claude/memory` |
| `global_storage_path` | Global memory path | `~/.claude/memory` |
| `embedding_model` | Sentence-transformer model name | `all-MiniLM-L6-v2` |
| `max_results` | Max results returned by recall | `10` |
| `similarity_threshold` | Min similarity score (0-1) for vector search | `0.3` |
| `auto_tag` | Auto-detect tags from content | `true` |
| `default_scope` | `project` or `global` | `project` |

## Backend Comparison

| Feature | Python (ChromaDB) | PowerShell (SQLite FTS5) |
|---------|-------------------|--------------------------|
| Semantic search | Yes (vector embeddings) | No (keyword matching) |
| Full-text search | Yes | Yes |
| Dependencies | chromadb, sentence-transformers | None (SQLite built-in) |
| Offline support | Yes (local model) | Yes |
| Cross-platform | Linux, macOS, Windows | Linux, macOS, Windows |
| Speed (store) | ~100ms | ~5ms |
| Speed (recall) | ~200ms | ~10ms |

## SQLite Schema

Both backends share the same SQLite database for metadata:

```sql
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    source TEXT DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- FTS5 virtual table for full-text search (PowerShell backend)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    tags,
    content=memories,
    content_rowid=rowid
);
```

## CLI Reference

### Python

```bash
# Store
python3 memory.py remember "text to remember" --tags "tag1,tag2" --scope project

# Recall
python3 memory.py recall "search query" --limit 5 --scope project

# Forget
python3 memory.py forget "query or id" --scope project

# List
python3 memory.py list --scope project --tags "tag1"

# Status
python3 memory.py status

# Setup (install dependencies)
python3 memory.py setup
```

### PowerShell

```powershell
# Store
pwsh memory.ps1 -Command remember -Content "text to remember" -Tags "tag1,tag2" -Scope project

# Recall
pwsh memory.ps1 -Command recall -Query "search query" -Limit 5 -Scope project

# Forget
pwsh memory.ps1 -Command forget -Query "query or id" -Scope project

# List
pwsh memory.ps1 -Command list -Scope project -Tags "tag1"

# Status
pwsh memory.ps1 -Command status
```

## Tags

Tags are auto-detected from content when `auto_tag` is enabled. Common auto-tags:

| Tag | Triggered by |
|-----|-------------|
| `architecture` | database, schema, API, design, pattern |
| `preference` | prefer, like, always, never, style |
| `bug` | bug, fix, issue, error, crash |
| `decision` | decided, chose, because, rationale |
| `config` | config, environment, setup, install |
| `api` | endpoint, request, response, REST, GraphQL |
