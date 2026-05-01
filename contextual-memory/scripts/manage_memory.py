#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from contextual_memory import (
    CandidateStatus,
    ContextPacker,
    MemoryCandidate,
    MemoryRecord,
    OllamaEmbedder,
    SQLiteMemoryStore,
)


MEMORY_HOME = Path.home() / ".codex" / "memory"
DEFAULT_DB = MEMORY_HOME / "contextual_memory.sqlite3"
MANAGED_VENV = MEMORY_HOME / "contextual-memory-venv"
SQLITE_VEC_VERSION = "0.1.9"
DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "embeddinggemma"
SAFE_AUTO_COMMIT_SOURCES = {"user_stated", "tool_verified"}


def managed_python() -> Path:
    return MANAGED_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_managed_python() -> bool:
    if Path(sys.prefix).absolute() == MANAGED_VENV.absolute():
        return True
    return Path(sys.executable).absolute() == managed_python().absolute()


def run(cmd: list[str], check: bool = False, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    stdout = subprocess.DEVNULL if quiet else subprocess.PIPE
    stderr = subprocess.DEVNULL if quiet else subprocess.PIPE
    return subprocess.run(cmd, check=check, text=True, stdout=stdout, stderr=stderr)


def sqlite_vec_dependency_is_pinned() -> bool:
    if not managed_python().exists():
        return False
    code = (
        "import importlib.metadata as metadata; "
        f"raise SystemExit(0 if metadata.version('sqlite-vec') == '{SQLITE_VEC_VERSION}' else 1)"
    )
    return run([str(managed_python()), "-c", code], quiet=True).returncode == 0


def ensure_managed_venv() -> None:
    MEMORY_HOME.mkdir(parents=True, exist_ok=True)
    if not managed_python().exists():
        subprocess.run([sys.executable, "-m", "venv", str(MANAGED_VENV)], check=True)
    if sqlite_vec_dependency_is_pinned():
        return
    subprocess.run(
        [
            str(managed_python()),
            "-m",
            "pip",
            "install",
            "-q",
            f"sqlite-vec=={SQLITE_VEC_VERSION}",
        ],
        check=True,
    )


def reexec_in_managed_venv() -> None:
    env = os.environ.copy()
    scripts_dir = str(Path(__file__).resolve().parent)
    env["PYTHONPATH"] = scripts_dir + os.pathsep + env.get("PYTHONPATH", "")
    os.execve(str(managed_python()), [str(managed_python()), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def command_needs_python_runtime(args: argparse.Namespace) -> bool:
    if args.command == "doctor":
        return bool(args.fix)
    return True


def command_needs_ollama(args: argparse.Namespace) -> bool:
    if args.command in {"init", "remember", "search", "context", "embed"}:
        return True
    return args.command == "candidates" and getattr(args, "candidate_command", "") == "commit"


def bootstrap_if_needed(args: argparse.Namespace) -> None:
    if command_needs_python_runtime(args):
        ensure_managed_venv()
        if not is_managed_python():
            reexec_in_managed_venv()
    if command_needs_ollama(args) or (args.command == "doctor" and args.fix):
        ensure_ollama(args.model if hasattr(args, "model") else DEFAULT_MODEL, fix=True)


def sqlite_vec_status() -> tuple[bool, str]:
    python = managed_python() if managed_python().exists() else Path(sys.executable)
    code = (
        "import sqlite3, sqlite_vec; "
        "conn=sqlite3.connect(':memory:'); "
        "conn.enable_load_extension(True); "
        "sqlite_vec.load(conn); "
        "conn.enable_load_extension(False); "
        "conn.execute('create virtual table v using vec0(e float[3])'); "
        "print(sqlite_vec.__file__)"
    )
    result = run([str(python), "-c", code])
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    return False, (result.stderr or result.stdout or "sqlite-vec unavailable").strip()


def fts5_status() -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("create virtual table t using fts5(x)")
    except sqlite3.Error as exc:
        return False, str(exc)
    return True, "available"


def ollama_path() -> str | None:
    return shutil.which("ollama")


def ensure_ollama(model: str, fix: bool) -> None:
    binary = ollama_path()
    if not binary and fix and platform.system() == "Darwin" and shutil.which("brew"):
        subprocess.run(["brew", "install", "ollama"], check=True)
        binary = ollama_path()
    if not binary:
        raise SystemExit("Ollama is missing. Install it from https://ollama.com or run `brew install ollama`.")

    if not ollama_responds(binary):
        start_ollama(binary)
    if not ollama_responds(binary):
        raise SystemExit("Ollama is installed but not responding. Start it with `ollama serve` and retry.")
    if fix and not ollama_model_exists(binary, model):
        subprocess.run([binary, "pull", model], check=True)
    if not ollama_model_exists(binary, model):
        raise SystemExit(f"Ollama model `{model}` is missing. Run `ollama pull {model}`.")


def ollama_responds(binary: str) -> bool:
    return run([binary, "list"], quiet=True).returncode == 0


def start_ollama(binary: str) -> None:
    with open(os.devnull, "wb") as devnull:
        try:
            subprocess.Popen([binary, "serve"], stdout=devnull, stderr=devnull, start_new_session=True)
        except OSError:
            return
    time.sleep(2)


def ollama_model_exists(binary: str, model: str) -> bool:
    result = run([binary, "show", model], quiet=True)
    return result.returncode == 0


def embedding_dimension(model: str) -> int:
    vector = OllamaEmbedder(model=model).embed("contextual memory dimension probe")
    if not vector:
        raise RuntimeError("Ollama returned an empty embedding.")
    return len(vector)


def open_store(db: Path, model: str = DEFAULT_MODEL) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(db)


def commit_memory(store: SQLiteMemoryStore, record: MemoryRecord, model: str) -> tuple[MemoryRecord, int]:
    memory = store.insert_memory(record)
    embeddings = store.embed_memory(memory.memory_id or "", OllamaEmbedder(model=model), DEFAULT_PROVIDER, model)
    return memory, len(embeddings)


def cmd_doctor(args: argparse.Namespace) -> int:
    if args.fix:
        ensure_managed_venv()

    print(f"Memory home: {MEMORY_HOME}")
    print(f"Default DB: {DEFAULT_DB}")
    print(f"Managed venv: {MANAGED_VENV}")
    print(f"Managed Python: {managed_python()}")

    venv_ok = managed_python().exists()
    print(f"venv: {'ok' if venv_ok else 'missing'}")

    fts_ok, fts_detail = fts5_status()
    print(f"sqlite fts5: {'ok' if fts_ok else 'missing'} ({fts_detail})")

    vec_ok, vec_detail = sqlite_vec_status()
    print(f"sqlite-vec: {'ok' if vec_ok else 'missing'} ({vec_detail})")

    binary = ollama_path()
    print(f"ollama: {'ok' if binary else 'missing'} ({binary or 'not found'})")
    responds = False
    model_ok = False
    if binary:
        responds = ollama_responds(binary)
        if args.fix and not responds:
            start_ollama(binary)
            responds = ollama_responds(binary)
        print(f"ollama service: {'ok' if responds else 'not responding'}")
        if responds:
            if args.fix and not ollama_model_exists(binary, args.model):
                subprocess.run([binary, "pull", args.model], check=True)
            model_ok = ollama_model_exists(binary, args.model)
            print(f"ollama model {args.model}: {'ok' if model_ok else 'missing'}")
    return 0 if fts_ok and vec_ok and bool(binary) and responds and model_ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    dimensions = embedding_dimension(args.model)
    store = SQLiteMemoryStore(args.db, vector_enabled=True, vector_dimensions=dimensions)
    print(f"Initialized: {args.db}")
    print(f"Embedding model: {args.model} ({dimensions} dimensions)")
    if store.vector_available:
        print("Vector search: ok")
    else:
        print(f"Vector search: degraded ({store.vector_error or 'sqlite-vec unavailable'})")
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    store = open_store(args.db, args.model)
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    record = MemoryRecord(
        user_id=args.user_id,
        kind=args.kind,
        text=args.text,
        metadata=metadata,
        source=args.source,
        confidence=args.confidence,
        importance=args.importance,
    )
    conflicts = store.find_conflicts(record)
    safe = (
        record.source in SAFE_AUTO_COMMIT_SOURCES
        and record.confidence >= args.auto_commit_confidence
        and not conflicts
    )
    if not safe:
        reason = "Memory source is not safe for auto-commit."
        if conflicts:
            reason = "Conflicts with existing memory."
        elif record.confidence < args.auto_commit_confidence:
            reason = "Memory confidence is below auto-commit threshold."
        candidate = store.stage_candidate(
            MemoryCandidate(
                record=record,
                conflict_memory_ids=[memory.memory_id for memory in conflicts if memory.memory_id],
                reason=reason,
            )
        )
        print(f"STAGED {candidate.candidate_id}: {reason}")
        return 0

    memory, embedding_count = commit_memory(store, record, args.model)
    print(f"COMMITTED {memory.memory_id}")
    print(f"Embeddings: {embedding_count}")
    print(f"Vector search: {'ok' if store.vector_available else 'degraded'}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = open_store(args.db, args.model)
    results = store.search_memory_results(
        args.query,
        user_id=args.user_id,
        limit=args.limit,
        embedder=OllamaEmbedder(model=args.model),
        provider=DEFAULT_PROVIDER,
        model=args.model,
    )
    if args.format == "json":
        print(
            json.dumps(
                [
                    {
                        "memory_id": item.memory.memory_id,
                        "kind": item.memory.kind,
                        "text": item.memory.text,
                        "score": item.score,
                        "lexical_score": item.lexical_score,
                        "vector_score": item.vector_score,
                        "sources": item.sources,
                        "chunk_id": item.chunk_id,
                    }
                    for item in results
                ],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for item in results:
            sources = ",".join(item.sources)
            print(f"{item.score:.3f}\t{sources}\t{item.memory.kind}\t{item.memory.memory_id}\t{item.memory.text}")
    if not store.vector_available:
        print(f"warning: vector search degraded ({store.vector_error or 'no vector table'})", file=sys.stderr)
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    store = open_store(args.db, args.model)
    results = store.search_memory_results(
        args.query,
        user_id=args.user_id,
        limit=args.limit,
        embedder=OllamaEmbedder(model=args.model),
        provider=DEFAULT_PROVIDER,
        model=args.model,
    )
    session = store.get_session(args.session_id, user_id=args.user_id)
    recent = store.recent_turns(args.session_id, user_id=args.user_id, limit=args.recent_turn_limit)
    staged = store.list_candidates(user_id=args.user_id)
    packet = ContextPacker().build(
        session,
        args.query,
        [item.memory for item in results],
        recent,
        staged,
        max_context_tokens=args.max_context_tokens,
    )
    print(
        json.dumps(
            {
                "messages": [{"role": message.role, "content": message.content} for message in packet.messages],
                "included": packet.token_budget_report.get("included", []),
                "dropped": packet.token_budget_report.get("dropped", []),
                "retrieved_memory_ids": [memory.memory_id for memory in packet.retrieved_memories],
                "vector_available": store.vector_available,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_embed_backfill(args: argparse.Namespace) -> int:
    store = open_store(args.db, args.model)
    rows = store.conn.execute(
        """
        select distinct c.memory_id
        from memory_chunks c
        left join memory_embeddings e
          on e.chunk_id = c.id and e.provider = ? and e.model = ?
        join memories m on m.id = c.memory_id
        where e.id is null and m.user_id = ? and m.stale = 0
        """,
        (DEFAULT_PROVIDER, args.model, args.user_id),
    ).fetchall()
    count = 0
    for row in rows:
        count += len(store.embed_memory(row["memory_id"], OllamaEmbedder(model=args.model), DEFAULT_PROVIDER, args.model))
    print(f"Backfilled embeddings: {count}")
    print(f"Vector search: {'ok' if store.vector_available else 'degraded'}")
    return 0


def cmd_candidates_list(args: argparse.Namespace) -> int:
    store = open_store(args.db)
    candidates = store.list_candidates(user_id=args.user_id, status=args.status)
    for candidate in candidates:
        print(f"{candidate.candidate_id}\t{candidate.action}\t{candidate.record.kind}\t{candidate.reason}\t{candidate.record.text}")
    return 0


def cmd_candidates_commit(args: argparse.Namespace) -> int:
    store = open_store(args.db, args.model)
    candidate = store.get_candidate(args.candidate_id)
    if not candidate:
        print(f"Unknown candidate: {args.candidate_id}", file=sys.stderr)
        return 1
    memory, embedding_count = commit_memory(store, candidate.record, args.model)
    store.set_candidate_status(args.candidate_id, CandidateStatus.COMMITTED)
    print(f"COMMITTED {memory.memory_id}")
    print(f"Embeddings: {embedding_count}")
    return 0


def cmd_candidates_reject(args: argparse.Namespace) -> int:
    store = open_store(args.db)
    if not store.get_candidate(args.candidate_id):
        print(f"Unknown candidate: {args.candidate_id}", file=sys.stderr)
        return 1
    store.set_candidate_status(args.candidate_id, CandidateStatus.IGNORED)
    print(f"REJECTED {args.candidate_id}")
    return 0


def add_common_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)


def add_model(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage durable contextual memory with Ollama and sqlite-vec.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check or install managed memory dependencies.")
    doctor.add_argument("--fix", action="store_true", help="Install missing managed dependencies.")
    add_model(doctor)
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="Create or migrate the memory database.")
    add_common_db(init)
    add_model(init)
    init.set_defaults(func=cmd_init)

    remember = sub.add_parser("remember", help="Commit or stage a memory.")
    add_common_db(remember)
    add_model(remember)
    remember.add_argument("--text", required=True)
    remember.add_argument("--kind", default="fact")
    remember.add_argument("--source", default="user_stated")
    remember.add_argument("--confidence", type=float, default=0.95)
    remember.add_argument("--importance", type=float, default=0.7)
    remember.add_argument("--user-id", default="default")
    remember.add_argument("--metadata-json", default="")
    remember.add_argument("--auto-commit-confidence", type=float, default=0.75)
    remember.set_defaults(func=cmd_remember)

    search = sub.add_parser("search", help="Search committed memories.")
    add_common_db(search)
    add_model(search)
    search.add_argument("--query", required=True)
    search.add_argument("--user-id", default="default")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--format", choices=("table", "json"), default="table")
    search.set_defaults(func=cmd_search)

    context = sub.add_parser("context", help="Emit a compact context packet.")
    add_common_db(context)
    add_model(context)
    context.add_argument("--session-id", required=True)
    context.add_argument("--query", required=True)
    context.add_argument("--user-id", default="default")
    context.add_argument("--limit", type=int, default=8)
    context.add_argument("--recent-turn-limit", type=int, default=6)
    context.add_argument("--max-context-tokens", type=int, default=6000)
    context.set_defaults(func=cmd_context)

    embed = sub.add_parser("embed", help="Embedding maintenance commands.")
    embed_sub = embed.add_subparsers(dest="embed_command", required=True)
    backfill = embed_sub.add_parser("backfill", help="Generate missing memory embeddings.")
    add_common_db(backfill)
    add_model(backfill)
    backfill.add_argument("--user-id", default="default")
    backfill.set_defaults(func=cmd_embed_backfill)

    candidates = sub.add_parser("candidates", help="Manage staged memory candidates.")
    candidates_sub = candidates.add_subparsers(dest="candidate_command", required=True)
    candidates_list = candidates_sub.add_parser("list")
    add_common_db(candidates_list)
    candidates_list.add_argument("--user-id", default="default")
    candidates_list.add_argument("--status", choices=("staged", "committed", "ignored"), default="staged")
    candidates_list.set_defaults(func=cmd_candidates_list)

    candidates_commit = candidates_sub.add_parser("commit")
    add_common_db(candidates_commit)
    add_model(candidates_commit)
    candidates_commit.add_argument("candidate_id")
    candidates_commit.set_defaults(func=cmd_candidates_commit)

    candidates_reject = candidates_sub.add_parser("reject")
    add_common_db(candidates_reject)
    candidates_reject.add_argument("candidate_id")
    candidates_reject.set_defaults(func=cmd_candidates_reject)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    bootstrap_if_needed(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
