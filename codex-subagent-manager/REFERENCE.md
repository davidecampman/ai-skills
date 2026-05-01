# Codex Subagent Manager — Reference

## Bundled Layout

```text
codex-subagent-manager/
├── SKILL.md
├── REFERENCE.md
├── scripts/
│   └── manage_agents.py
└── agents/
    ├── README.md
    ├── CONTRIBUTING.md
    ├── LICENSE
    └── categories/
        └── */*.toml
```

The bundled `agents/` directory is a copy of the `awesome-codex-subagents`
collection and retains its MIT license notice.

## Required Agent Shape

Each standalone Codex custom agent file must be valid TOML and define:

- `name`: unique agent name; match the filename stem unless there is a documented exception.
- `description`: concise trigger guidance for when the parent agent should use it.
- `developer_instructions`: task-shaped behavior and output contract.

Common optional fields include `nickname_candidates`, `model`,
`model_reasoning_effort`, `sandbox_mode`, `mcp_servers`, and `skills.config`.

Prefer top-level `developer_instructions = """..."""`. Do not document
`[instructions] text` as the Codex custom-agent format.

## Quality-First Model Policy

Use a quality-first default for this collection:

- Prefer `gpt-5.4` with high reasoning for high-risk or complex work: security,
  architecture, infrastructure, payments, identity, data safety, migrations,
  concurrency, reliability, and broad implementation.
- Use lighter models only for low-risk search, synthesis, triage, install
  planning, and documentation lookup.
- Flag `gpt-5.3-codex-spark` for portability because it is a research-preview
  model and may not be available to every user. Do not bulk-rewrite Spark pins
  unless the user asks.
- Treat `gpt-5.5` as a possible user/account default rather than a mandatory
  pin for a public repository.
- If cost sensitivity matters, propose a separate cost-optimized policy instead
  of silently changing quality-first choices.

## Sandbox Policy

Treat `sandbox_mode` as a custom-agent default or preference. Do not describe it
as a hard guarantee that the agent can or cannot access files, because Codex can
inherit or override sandbox policy from the parent runtime session.

Recommended defaults:

- `read-only`: reviewers, auditors, researchers, planners, designers, coordinators, and evidence gatherers.
- `workspace-write`: implementers, fixers, test authors, documentation writers, and agents expected to edit files.

Flag role mismatches:

- A read-only agent whose description promises implementation or file-changing work.
- A workspace-write agent whose role is only evidence gathering, review, or advice.
- Instructions that say "implement" when the intended role is advisory.

## MCP Policy

MCP entries are useful but create environment assumptions. Flag:

- hardcoded local URLs such as `http://localhost:3000/mcp`
- MCP servers not documented as prerequisites
- agents whose role depends on a tool but whose description does not say so

Prefer making MCP prerequisites explicit in installation guidance.

## Documentation Policy

Repository documentation should stay synchronized with the agent files:

- Main README links every agent TOML.
- Category READMEs link each local agent TOML, not just render names in code spans.
- README examples match the actual Codex custom-agent schema.
- Sandbox documentation explains inheritance and runtime overrides.

## Install Policy

Always dry-run first. Show:

- selected agent names and source paths
- target scope and directory
- conflicts with existing files
- exact files that would be copied

Copy only selected `.toml` files. Never install the whole collection by default.
