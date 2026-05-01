---
name: codex-subagent-manager
description: >
  Manage Codex custom subagent collections and bundled `.toml` agents.
  Use when the user wants to inventory, validate, audit, recommend, install,
  update, or review Codex subagents; check model, reasoning, sandbox, MCP,
  and documentation policy; or safely copy selected agents into global or
  project `.codex/agents` directories.
allowed-tools: Bash(python3 *), Bash(python *), Bash(cp *), Bash(mkdir *), Read, Write, Grep, Glob
---

# Codex Subagent Manager

Manage the bundled Codex subagents in `agents/` and other repositories that use
the same `categories/*/*.toml` layout.

## Commands

| Command | Description |
|---------|-------------|
| `inventory` | List available agents with category, model, sandbox, and MCP flags |
| `validate` | Check TOML validity, required fields, unique names, and README links |
| `audit` | Produce quality-first model, sandbox, MCP, and documentation findings |
| `install` | Copy selected agents into global or project Codex agent directories |

## How to Execute

Use the bundled manager script:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" <command> --repo "${CLAUDE_SKILL_DIR}/agents"
```

If `CLAUDE_SKILL_DIR` is unavailable, run the script from this skill folder and
use `--repo agents`.

## Common Workflows

### Inventory bundled agents

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" inventory --repo "${CLAUDE_SKILL_DIR}/agents" --format table
```

### Validate bundled agents

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" validate --repo "${CLAUDE_SKILL_DIR}/agents"
```

### Audit quality and settings

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" audit --repo "${CLAUDE_SKILL_DIR}/agents" --policy quality-first
```

### Dry-run a global install

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" install \
  --repo "${CLAUDE_SKILL_DIR}/agents" \
  --agents reviewer docs-researcher code-mapper \
  --scope global \
  --dry-run
```

### Dry-run a project install

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/manage_agents.py" install \
  --repo "${CLAUDE_SKILL_DIR}/agents" \
  --agents reviewer docs-researcher code-mapper \
  --scope project \
  --project-dir /path/to/project \
  --dry-run
```

Only run the same install command without `--dry-run` after the user confirms
the exact agents and scope.

## Selection Guidance

Use the smallest role set that covers the user's goal. Prefer one broad
implementer plus one or two focused reviewers over many overlapping specialists.

- `--scope global` targets `~/.codex/agents`.
- `--scope project` targets `<project-dir>/.codex/agents`.
- Existing target files are skipped unless `--overwrite` is passed.
- After installing, recommend restarting or refreshing Codex if agents are not discovered.

## Review Guidance

Read [REFERENCE.md](REFERENCE.md) before making model, sandbox, MCP, or schema
recommendations. Treat `sandbox_mode` as an agent default or preference, not as
a hard security guarantee, because parent runtime policy can override custom
agent defaults.

When reporting issues, lead with concrete findings:

- schema or TOML validity
- duplicate names or filename/name mismatches
- documentation snippets that do not match Codex custom-agent schema
- model portability or quality-policy mismatches
- sandbox/read-write mismatches with the role description
- MCP assumptions that require local services or setup

Do not rewrite all agents automatically. Propose focused findings first, then
edit only the requested subset.
