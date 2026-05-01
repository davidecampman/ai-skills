# AI Skills

A collection of reusable AI skills for Claude Code and other AI assistants.

## Skills

| Skill | Description |
|-------|-------------|
| [codex-subagent-manager](codex-subagent-manager/) | Manage, validate, audit, and install bundled Codex custom subagents. Includes the `awesome-codex-subagents` TOML agent collection. |
| [rag-memory](rag-memory/) | Persistent RAG-based memory system for storing and retrieving knowledge across sessions. Supports Python (vector search via ChromaDB) and PowerShell (SQLite FTS5 fallback). |

## Usage

Each skill lives in its own folder with:

- **SKILL.md** — Skill definition and execution instructions
- **REFERENCE.md** — Detailed API docs and configuration
- **scripts/** — Backend implementation scripts

To install a skill in your project, copy its folder into `.claude/skills/` or reference it from your Claude Code configuration.

## Contributing

To add a new skill, create a folder at the repo root with the structure above.
