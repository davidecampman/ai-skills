# AI Skills

A Codex-focused skill collection for managing custom subagents and related
automation. Each skill is self-contained: instructions live in `SKILL.md`,
deeper reference material lives in `REFERENCE.md`, and repeatable automation
lives in `scripts/`.

## Skills

| Skill | Description |
|-------|-------------|
| [codex-subagent-manager](codex-subagent-manager/) | Manage, validate, audit, and install bundled Codex custom subagents. Includes the `awesome-codex-subagents` TOML agent collection. |

## Codex Subagent Manager

`codex-subagent-manager` bundles 136 Codex custom agents from
`awesome-codex-subagents` and includes a standard-library Python manager for
inventory, validation, auditing, and safe install dry-runs.

Useful commands:

```bash
python3 codex-subagent-manager/scripts/manage_agents.py inventory \
  --repo codex-subagent-manager/agents \
  --format table
```

```bash
python3 codex-subagent-manager/scripts/manage_agents.py validate \
  --repo codex-subagent-manager/agents
```

```bash
python3 codex-subagent-manager/scripts/manage_agents.py audit \
  --repo codex-subagent-manager/agents \
  --policy quality-first
```

Dry-run a global Codex agent install:

```bash
python3 codex-subagent-manager/scripts/manage_agents.py install \
  --repo codex-subagent-manager/agents \
  --agents reviewer docs-researcher code-mapper \
  --scope global \
  --dry-run
```

Remove `--dry-run` only after confirming the exact agents and target scope.

## Usage

Each skill lives in its own folder with:

- **SKILL.md** — Skill definition and execution instructions
- **REFERENCE.md** — Detailed API docs and configuration
- **scripts/** — Backend implementation scripts

To install `codex-subagent-manager` for Codex skill discovery, copy the skill
folder into `~/.codex/skills/`.

## Contributing

To add a new skill, create a folder at the repo root with the structure above.
Keep skills lean: avoid extra docs unless they are directly used by the skill.
