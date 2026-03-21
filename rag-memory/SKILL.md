---
name: rag-memory
description: >
  Persistent RAG-based memory system for storing and retrieving knowledge across sessions.
  Use when the user wants to remember facts, decisions, preferences, or project context.
  Triggers on keywords: remember, recall, forget, memory, memorize, what do you know about.
allowed-tools: Bash(python *), Bash(pwsh *), Bash(powershell *), Read, Write, Grep, Glob
argument-hint: "[remember|recall|forget] [content or query]"
---

# RAG Memory Skill

A cross-platform persistent memory system using retrieval-augmented generation (RAG).
Works with both Python (full vector search) and PowerShell (full-text search fallback).

## Commands

| Command | Description |
|---------|-------------|
| `/rag-memory remember <text>` | Store a memory with optional tags |
| `/rag-memory recall <query>` | Search memories by semantic similarity or keywords |
| `/rag-memory forget <query>` | Remove memories matching a query |
| `/rag-memory list` | List all stored memories |
| `/rag-memory status` | Show memory store stats and backend info |

## How to Execute

### 1. Detect available backend

Check which runtime is available, preferring Python (full RAG) over PowerShell (FTS fallback):

```bash
# Try Python first
python3 --version 2>/dev/null || python --version 2>/dev/null

# Fall back to PowerShell
pwsh --version 2>/dev/null || powershell -Command "$PSVersionTable.PSVersion" 2>/dev/null
```

### 2. Route to the correct script

**Python backend** (full vector embeddings via ChromaDB + sentence-transformers):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/memory.py" <command> [arguments]
```

**PowerShell backend** (SQLite FTS5 full-text search):

```bash
pwsh -File "${CLAUDE_SKILL_DIR}/scripts/memory.ps1" -Command <command> [arguments]
```

### 3. Command routing

For `$ARGUMENTS`:

- If starts with `remember` → store the remaining text as a memory
- If starts with `recall` → search for the remaining text
- If starts with `forget` → delete memories matching the remaining text
- If starts with `list` → list all memories
- If starts with `status` → show store statistics
- If no command keyword → treat as a `recall` query

### 4. Data storage location

Memories are stored in the project directory by default:

```
.claude/memory/
├── memories.db          # SQLite database (shared by both backends)
├── chroma/              # ChromaDB vector store (Python backend only)
└── config.json          # Memory configuration
```

For global (cross-project) memories, use `~/.claude/memory/`.

### 5. Memory format

Each memory has:
- `id` — Unique identifier (UUID)
- `content` — The text content
- `tags` — Comma-separated tags for categorization
- `source` — Where the memory came from (user, auto, project)
- `created_at` — ISO 8601 timestamp
- `updated_at` — ISO 8601 timestamp

### 6. Auto-capture guidance

When Claude encounters important information during a session, it should proactively
suggest storing it. Key candidates for auto-capture:

- Architecture decisions and their rationale
- User preferences and coding style choices
- Environment-specific configuration notes
- Bug root causes and their fixes
- API quirks or undocumented behaviors

Prompt the user: "This seems worth remembering. Shall I store it?"

### 7. Output format

Always return results in a clean, readable format:

**For recall:**
```
Found N memories matching "query":

1. [2024-01-15] (tags: architecture, backend)
   We decided to use PostgreSQL for the main database because...

2. [2024-01-10] (tags: preference)
   User prefers tabs over spaces, 4-width indentation...
```

**For remember:**
```
Stored memory (id: abc123)
Tags: [auto-detected tags]
Content: [stored content preview]
```

See [REFERENCE.md](REFERENCE.md) for detailed API documentation and configuration options.
