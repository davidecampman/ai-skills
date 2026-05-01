#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("name", "description", "developer_instructions")
KNOWN_MODELS = {
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
}
KNOWN_REASONING = {"low", "medium", "high", "xhigh"}
KNOWN_SANDBOX = {"read-only", "workspace-write", "danger-full-access"}


@dataclass
class Agent:
    name: str
    description: str
    path: str
    category: str
    model: str | None
    model_reasoning_effort: str | None
    sandbox_mode: str | None
    has_mcp: bool


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    hint: str | None = None

    def as_output(self) -> str:
        loc = ""
        if self.path:
            loc = f" ({self.path}"
            if self.line:
                loc += f":{self.line}"
            loc += ")"
        text = f"[{self.severity}] {self.code}{loc}: {self.message}"
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text


class RepoError(Exception):
    pass


def relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def line_number(path: Path, pattern: str) -> int | None:
    try:
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern in line:
                return index
    except OSError:
        return None
    return None


def agent_files(repo: Path) -> list[Path]:
    categories = repo / "categories"
    if not categories.is_dir():
        raise RepoError(f"{repo} does not look like an awesome-codex-subagents repo; missing categories/")
    return sorted(categories.glob("*/*.toml"))


def load_agent(path: Path, repo: Path) -> tuple[Agent | None, dict[str, Any] | None, Issue | None]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface TOML parser detail.
        return None, None, Issue(
            "ERROR",
            "TOML_PARSE",
            f"Could not parse TOML: {exc}",
            relpath(path, repo),
            None,
        )

    name = data.get("name")
    description = data.get("description")
    if isinstance(name, str) and isinstance(description, str):
        agent = Agent(
            name=name,
            description=description,
            path=relpath(path, repo),
            category=path.parent.name,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            model_reasoning_effort=(
                data.get("model_reasoning_effort")
                if isinstance(data.get("model_reasoning_effort"), str)
                else None
            ),
            sandbox_mode=data.get("sandbox_mode") if isinstance(data.get("sandbox_mode"), str) else None,
            has_mcp=isinstance(data.get("mcp_servers"), dict) and bool(data.get("mcp_servers")),
        )
    else:
        agent = Agent(
            name=str(name or path.stem),
            description=str(description or ""),
            path=relpath(path, repo),
            category=path.parent.name,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            model_reasoning_effort=(
                data.get("model_reasoning_effort")
                if isinstance(data.get("model_reasoning_effort"), str)
                else None
            ),
            sandbox_mode=data.get("sandbox_mode") if isinstance(data.get("sandbox_mode"), str) else None,
            has_mcp=isinstance(data.get("mcp_servers"), dict) and bool(data.get("mcp_servers")),
        )
    return agent, data, None


def load_agents(repo: Path) -> tuple[list[Agent], dict[str, dict[str, Any]], list[Issue]]:
    agents: list[Agent] = []
    raw: dict[str, dict[str, Any]] = {}
    issues: list[Issue] = []
    files = agent_files(repo)
    if not files:
        issues.append(Issue("ERROR", "NO_AGENTS", "No categories/*/*.toml files found."))
        return agents, raw, issues

    for path in files:
        agent, data, issue = load_agent(path, repo)
        if issue:
            issues.append(issue)
            continue
        if agent and data is not None:
            agents.append(agent)
            raw[agent.path] = data
    return agents, raw, issues


def validate_agents(repo: Path) -> tuple[list[Agent], list[Issue]]:
    agents, raw, issues = load_agents(repo)
    names: dict[str, list[Agent]] = {}

    for agent in agents:
        path = repo / agent.path
        data = raw.get(agent.path, {})
        for field in REQUIRED_FIELDS:
            if not isinstance(data.get(field), str) or not data.get(field, "").strip():
                issues.append(
                    Issue(
                        "ERROR",
                        "MISSING_REQUIRED_FIELD",
                        f"Missing or empty required field `{field}`.",
                        agent.path,
                        line_number(path, field),
                    )
                )

        names.setdefault(agent.name, []).append(agent)
        if path.stem != agent.name:
            issues.append(
                Issue(
                    "ERROR",
                    "FILENAME_NAME_MISMATCH",
                    f"Filename stem `{path.stem}` does not match agent name `{agent.name}`.",
                    agent.path,
                    line_number(path, "name ="),
                )
            )

        if agent.model and agent.model not in KNOWN_MODELS:
            issues.append(
                Issue(
                    "WARN",
                    "UNKNOWN_MODEL",
                    f"Model `{agent.model}` is not in the known Codex model allowlist.",
                    agent.path,
                    line_number(path, "model ="),
                )
            )
        if agent.model_reasoning_effort and agent.model_reasoning_effort not in KNOWN_REASONING:
            issues.append(
                Issue(
                    "WARN",
                    "UNKNOWN_REASONING",
                    f"Reasoning effort `{agent.model_reasoning_effort}` is not in the known set.",
                    agent.path,
                    line_number(path, "model_reasoning_effort ="),
                )
            )
        if agent.sandbox_mode and agent.sandbox_mode not in KNOWN_SANDBOX:
            issues.append(
                Issue(
                    "WARN",
                    "UNKNOWN_SANDBOX",
                    f"Sandbox mode `{agent.sandbox_mode}` is not in the known set.",
                    agent.path,
                    line_number(path, "sandbox_mode ="),
                )
            )

    for name, duplicates in sorted(names.items()):
        if len(duplicates) > 1:
            paths = ", ".join(agent.path for agent in duplicates)
            issues.append(Issue("ERROR", "DUPLICATE_NAME", f"Agent name `{name}` appears in {paths}."))

    issues.extend(validate_docs(repo, agents))
    return agents, issues


def markdown_links(markdown: str) -> set[str]:
    return set(re.findall(r"\]\(([^)]+\.toml)\)", markdown))


def normalize_link(md_path: Path, link: str) -> str:
    if link.startswith("categories/"):
        return link
    return (md_path.parent / link).as_posix()


def validate_docs(repo: Path, agents: list[Agent]) -> list[Issue]:
    issues: list[Issue] = []
    all_agent_paths = {agent.path for agent in agents}
    readme = repo / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        links = {normalize_link(readme, link) for link in markdown_links(text)}
        missing = sorted(all_agent_paths - links)
        broken = sorted(link for link in links if link.startswith("categories/") and link not in all_agent_paths)
        if missing:
            issues.append(
                Issue(
                    "WARN",
                    "MAIN_README_MISSING_AGENT_LINKS",
                    f"README.md does not link {len(missing)} agent TOML file(s).",
                    "README.md",
                    None,
                    ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else ""),
                )
            )
        if broken:
            issues.append(
                Issue(
                    "WARN",
                    "MAIN_README_BROKEN_AGENT_LINKS",
                    f"README.md has {len(broken)} broken agent TOML link(s).",
                    "README.md",
                    None,
                    ", ".join(broken[:8]) + (" ..." if len(broken) > 8 else ""),
                )
            )
        if "[instructions]" in text and "developer_instructions" not in text[text.find("[instructions]") : text.find("[instructions]") + 500]:
            issues.append(
                Issue(
                    "WARN",
                    "README_OBSOLETE_SCHEMA_EXAMPLE",
                    "README.md appears to document `[instructions] text` instead of top-level `developer_instructions`.",
                    "README.md",
                    line_number(readme, "[instructions]"),
                    "Use `developer_instructions = \"\"\"...\"\"\"` in the custom-agent example.",
                )
            )
    else:
        issues.append(Issue("WARN", "MISSING_MAIN_README", "Repository has no README.md."))

    for category_dir in sorted((repo / "categories").glob("*")):
        if not category_dir.is_dir():
            continue
        md = category_dir / "README.md"
        category_paths = {relpath(path, repo) for path in sorted(category_dir.glob("*.toml"))}
        if not md.is_file():
            issues.append(
                Issue(
                    "WARN",
                    "MISSING_CATEGORY_README",
                    f"{relpath(category_dir, repo)} has no README.md.",
                    relpath(category_dir, repo),
                )
            )
            continue
        text = md.read_text(encoding="utf-8")
        links = {normalize_link(md, link) for link in markdown_links(text)}
        missing = sorted(category_paths - links)
        broken = sorted(link for link in links if link not in all_agent_paths)
        if missing:
            issues.append(
                Issue(
                    "WARN",
                    "CATEGORY_README_MISSING_AGENT_LINKS",
                    f"{relpath(md, repo)} does not link {len(missing)} local agent TOML file(s).",
                    relpath(md, repo),
                    None,
                    ", ".join(Path(path).name for path in missing[:8]) + (" ..." if len(missing) > 8 else ""),
                )
            )
        if broken:
            issues.append(
                Issue(
                    "WARN",
                    "CATEGORY_README_BROKEN_AGENT_LINKS",
                    f"{relpath(md, repo)} has {len(broken)} broken agent TOML link(s).",
                    relpath(md, repo),
                    None,
                    ", ".join(broken[:8]) + (" ..." if len(broken) > 8 else ""),
                )
            )
    return issues


def audit_agents(repo: Path, policy: str) -> list[Issue]:
    agents, validation_issues = validate_agents(repo)
    _, raw, _ = load_agents(repo)
    issues = [issue for issue in validation_issues if issue.severity in {"ERROR", "WARN"}]

    for agent in agents:
        path = repo / agent.path
        blob = f"{agent.name} {agent.description}".lower()
        if agent.model == "gpt-5.3-codex-spark":
            issues.append(
                Issue(
                    "P2",
                    "MODEL_SPARK_PORTABILITY",
                    "`gpt-5.3-codex-spark` is less portable for a public collection.",
                    agent.path,
                    line_number(path, "model ="),
                    "Quality-first policy may keep it, but call out Pro/research-preview availability and consider `gpt-5.4-mini` for portable light agents.",
                )
            )
        if policy == "quality-first" and agent.model != "gpt-5.4" and is_high_risk(agent):
            issues.append(
                Issue(
                    "P3",
                    "MODEL_UNDERPOWERED_QUALITY_FIRST",
                    "High-risk or complex role is not pinned to `gpt-5.4` under quality-first policy.",
                    agent.path,
                    line_number(path, "model ="),
                    "Use `gpt-5.4` for security, infrastructure, payments, data safety, architecture, and broad implementation roles unless portability is more important.",
                )
            )
        if policy == "quality-first" and agent.model == "gpt-5.4" and agent.model_reasoning_effort == "high" and is_low_risk_synthesis(agent):
            issues.append(
                Issue(
                    "INFO",
                    "MODEL_COST_REVIEW",
                    "Low-risk synthesis/planning role uses the strongest default.",
                    agent.path,
                    line_number(path, "model ="),
                    "This is acceptable for quality-first, but a cost-optimized policy could use `gpt-5.4-mini` or inherit the user default.",
                )
            )

        if agent.sandbox_mode == "read-only" and promises_implementation(agent):
            issues.append(
                Issue(
                    "P3",
                    "SANDBOX_READONLY_IMPLEMENTATION_WORDING",
                    "Read-only agent wording promises or strongly implies implementation.",
                    agent.path,
                    line_number(path, "description ="),
                    "Make the role advisory/review-only, or switch to `workspace-write` if it is expected to edit files.",
                )
            )
        if agent.sandbox_mode == "workspace-write" and evidence_only_role(agent):
            issues.append(
                Issue(
                    "P3",
                    "SANDBOX_WRITE_EVIDENCE_ROLE",
                    "Workspace-write agent appears to be primarily evidence gathering or review.",
                    agent.path,
                    line_number(path, "sandbox_mode ="),
                    "Prefer `read-only` unless the role must edit files.",
                )
            )

        data = raw.get(agent.path, {})
        for server_name, server in sorted((data.get("mcp_servers") or {}).items()):
            if not isinstance(server, dict):
                continue
            url = str(server.get("url", ""))
            if url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
                issues.append(
                    Issue(
                        "P3",
                        "MCP_HARDCODED_LOCAL_URL",
                        f"MCP server `{server_name}` assumes a local service at {url}.",
                        agent.path,
                        line_number(path, f"[mcp_servers.{server_name}]"),
                        "Document the prerequisite or make this agent advisory until the local MCP server is configured.",
                    )
                )
            else:
                issues.append(
                    Issue(
                        "INFO",
                        "MCP_PREREQUISITE",
                        f"MCP server `{server_name}` is required for full behavior.",
                        agent.path,
                        line_number(path, f"[mcp_servers.{server_name}]"),
                        "Mention MCP setup in installation guidance for this agent.",
                    )
                )

    issues.extend(template_duplication_findings(repo, agents, raw))
    issues.extend(readme_sandbox_findings(repo))
    return issues


def is_high_risk(agent: Agent) -> bool:
    name = agent.name.lower()
    desc = agent.description.lower()
    if agent.category == "03-infrastructure":
        return True
    name_keywords = (
        "security",
        "auditor",
        "architect",
        "payment",
        "fintech",
        "blockchain",
        "infrastructure",
        "terraform",
        "terragrunt",
        "kubernetes",
        "docker",
        "database",
        "postgres",
        "compliance",
        "incident",
        "sre",
        "m365",
    )
    desc_patterns = (
        r"\bidentity\b",
        r"\biam\b",
        r"\bauth(entication|orization)?\b",
        r"\bsecrets?\b",
        r"\brbac\b",
        r"\bprivilege\b",
        r"\bpayment\b",
        r"\bpci\b",
    )
    return any(keyword in name for keyword in name_keywords) or any(
        re.search(pattern, desc) for pattern in desc_patterns
    )


def is_low_risk_synthesis(agent: Agent) -> bool:
    text = f"{agent.category} {agent.name} {agent.description}".lower()
    low_risk = (
        "business-analyst",
        "content-marketer",
        "customer-success-manager",
        "project-manager",
        "scrum-master",
        "sales-engineer",
        "ux-researcher",
        "market-researcher",
        "trend-analyst",
        "knowledge-synthesizer",
        "task-distributor",
    )
    return any(token in text for token in low_risk)


def promises_implementation(agent: Agent) -> bool:
    desc = agent.description.lower()
    if "before implementation" in desc or "implementation-ready" in desc:
        return False
    patterns = (
        "review or implementation",
        "implementation across",
        "implementation work",
        "needs implementation",
        "hardening work",
    )
    return any(pattern in desc for pattern in patterns)


def evidence_only_role(agent: Agent) -> bool:
    desc = agent.description.lower()
    evidence_markers = ("evidence gathering", "reproduction", "review", "audit")
    implementation_markers = (
        "implement",
        "fix",
        "writer",
        "developer",
        "development",
        "engineering",
        "automator",
        "feature",
        "change",
        "improvement",
        "tool",
        "creator",
    )
    if any(marker in desc for marker in evidence_markers) and not any(marker in desc for marker in implementation_markers):
        return True
    return agent.name in {"browser-debugger"}


def template_duplication_findings(repo: Path, agents: list[Agent], raw: dict[str, dict[str, Any]]) -> list[Issue]:
    line_to_paths: dict[str, list[str]] = {}
    for agent in agents:
        text = raw.get(agent.path, {}).get("developer_instructions", "")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) < 40:
                continue
            line_to_paths.setdefault(stripped, []).append(agent.path)

    repeated = [
        (line, paths)
        for line, paths in line_to_paths.items()
        if len(paths) >= 12 and not line.startswith("- ")
    ]
    if not repeated:
        return []
    repeated.sort(key=lambda item: len(item[1]), reverse=True)
    line, paths = repeated[0]
    return [
        Issue(
            "INFO",
            "TEMPLATE_DUPLICATION",
            f"Instruction line is repeated across {len(paths)} agents, suggesting template-heavy role design.",
            paths[0],
            line_number(repo / paths[0], line),
            "Spot-check repeated families and add role-specific constraints where the shared skeleton hides meaningful differences.",
        )
    ]


def readme_sandbox_findings(repo: Path) -> list[Issue]:
    readme = repo / "README.md"
    if not readme.is_file():
        return []
    text = readme.read_text(encoding="utf-8")
    if "controls filesystem access" in text:
        return [
            Issue(
                "P2",
                "README_SANDBOX_OVERSTATED",
                "README wording implies `sandbox_mode` directly controls filesystem access.",
                "README.md",
                line_number(readme, "controls filesystem access"),
                "Explain that custom-agent sandbox settings can be inherited or overridden by parent runtime policy.",
            )
        ]
    return []


def cmd_inventory(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    agents, _, issues = load_agents(repo)
    if issues:
        print_issues(issues)
        return 1
    if args.format == "json":
        print(json.dumps([asdict(agent) for agent in agents], indent=2, sort_keys=True))
    else:
        print_table(agents)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    agents, issues = validate_agents(repo)
    print(f"Agents parsed: {len(agents)}")
    print(f"Errors: {sum(1 for issue in issues if issue.severity == 'ERROR')}")
    print(f"Warnings: {sum(1 for issue in issues if issue.severity == 'WARN')}")
    if issues:
        print()
        print_issues(issues)
    return 1 if any(issue.severity == "ERROR" for issue in issues) else 0


def cmd_audit(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    issues = audit_agents(repo, args.policy)
    print(f"Audit policy: {args.policy}")
    print(f"Findings: {len(issues)}")
    if issues:
        print()
        print_issues(issues)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    agents, _, issues = load_agents(repo)
    if any(issue.severity == "ERROR" for issue in issues):
        print_issues(issues)
        return 1

    requested = parse_agent_names(args.agents)
    by_name = {agent.name: agent for agent in agents}
    missing = sorted(name for name in requested if name not in by_name)
    if missing:
        print(f"Unknown agent(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.scope == "global":
        target_dir = Path.home() / ".codex" / "agents"
    else:
        project_dir = args.project_dir.resolve() if args.project_dir else Path.cwd()
        target_dir = project_dir / ".codex" / "agents"

    print(f"Scope: {args.scope}")
    print(f"Target: {target_dir}")
    print(f"Dry run: {args.dry_run}")
    print()

    exit_code = 0
    if not args.dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    for name in requested:
        agent = by_name[name]
        source = repo / agent.path
        dest = target_dir / f"{agent.name}.toml"
        if dest.exists() and not args.overwrite:
            print(f"SKIP conflict: {agent.name} -> {dest} already exists")
            exit_code = 2
            continue
        action = "WOULD COPY" if args.dry_run else "COPY"
        print(f"{action}: {source} -> {dest}")
        if not args.dry_run:
            shutil.copy2(source, dest)
    return exit_code


def parse_agent_names(values: list[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                names.append(item)
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            unique.append(name)
            seen.add(name)
    return unique


def print_table(agents: list[Agent]) -> None:
    headers = ("name", "category", "model", "effort", "sandbox", "mcp")
    rows = [
        (
            agent.name,
            agent.category,
            agent.model or "",
            agent.model_reasoning_effort or "",
            agent.sandbox_mode or "",
            "yes" if agent.has_mcp else "no",
        )
        for agent in agents
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_issues(issues: list[Issue]) -> None:
    severity_order = {"ERROR": 0, "P2": 1, "P3": 2, "WARN": 3, "INFO": 4}
    for issue in sorted(issues, key=lambda item: (severity_order.get(item.severity, 9), item.code, item.path or "")):
        print(issue.as_output())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage awesome-codex-subagents TOML agent collections.")
    sub = parser.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory", help="List available agents.")
    inventory.add_argument("--repo", type=Path, default=Path("."), help="Path to the agent repository.")
    inventory.add_argument("--format", choices=("table", "json"), default="table")
    inventory.set_defaults(func=cmd_inventory)

    validate = sub.add_parser("validate", help="Validate TOML schema and documentation coverage.")
    validate.add_argument("--repo", type=Path, default=Path("."), help="Path to the agent repository.")
    validate.set_defaults(func=cmd_validate)

    audit = sub.add_parser("audit", help="Audit model, sandbox, MCP, and documentation policy.")
    audit.add_argument("--repo", type=Path, default=Path("."), help="Path to the agent repository.")
    audit.add_argument("--policy", choices=("quality-first",), default="quality-first")
    audit.set_defaults(func=cmd_audit)

    install = sub.add_parser("install", help="Copy selected agents into a Codex agent directory.")
    install.add_argument("--repo", type=Path, default=Path("."), help="Path to the agent repository.")
    install.add_argument("--agents", nargs="+", required=True, help="Agent names, space or comma separated.")
    install.add_argument("--scope", choices=("global", "project"), required=True)
    install.add_argument("--project-dir", type=Path, help="Project directory for --scope project. Defaults to cwd.")
    install.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files.")
    install.add_argument("--overwrite", action="store_true", help="Overwrite existing target agent files.")
    install.set_defaults(func=cmd_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RepoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
