"""Command line entry point: ``matrix serve|doctor|benchmark-model|backup|pair-code``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .access import RemoteAccessManager, StartupTaskManager
from .config import MatrixConfig
from .core.models import Principal
from .errors import MatrixError, ModelUnavailable, ValidationFailure
from .models.base import is_local_endpoint
from .models.benchmark import DEFAULT_BENCHMARK_CASES, run_model_benchmark
from .runtime import MatrixRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrix",
        description="MATRIX - local-first personal AI assistant (Windows + Android companion)",
    )
    parser.add_argument("--version", action="version", version=f"matrix {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the local API and JARVIS dashboard")
    serve.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="port (default 8765)")
    serve.add_argument("--no-background", action="store_true", help="disable the scheduler loop")

    sub.add_parser("doctor", help="print a health/diagnostics report (no secrets)")

    benchmark = sub.add_parser(
        "benchmark-model", help="benchmark a configured local model (plans are never executed)"
    )
    benchmark.add_argument(
        "--max-cases", type=int, default=len(DEFAULT_BENCHMARK_CASES),
        help=f"number of English/Hindi test prompts (1-{len(DEFAULT_BENCHMARK_CASES)}; default: all)",
    )

    backup = sub.add_parser("backup", help="create and verify a SQLite backup")
    backup.add_argument("--out", default=None, help="destination .db path")

    sub.add_parser("pair-code", help="issue a single-use device pairing code")

    remote = sub.add_parser("remote-access", help="manage private Tailscale access (never public Funnel)")
    remote.add_argument("action", choices=["status", "enable", "disable"])

    startup = sub.add_parser("startup", help="manage per-user Windows logon startup")
    startup.add_argument("action", choices=["status", "install", "uninstall"])
    return parser


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    config = MatrixConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    runtime = MatrixRuntime.build(config)
    app = create_app(runtime=runtime, start_background=not args.no_background)
    url = f"http://{config.host}:{config.port}"
    print(f"MATRIX {__version__} -> {url}")
    print(f"Database: {config.db_path}")
    if config.owner_token_generated:
        print(f"Owner token written to: {config.home / 'owner_token.txt'}")
    print("Open the URL, paste the owner token once, and the dashboard will remember it locally.")
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level.lower())
    return 0


async def _doctor() -> int:
    runtime = MatrixRuntime.build(MatrixConfig.from_env())
    try:
        health = await runtime.health()
        payload = {
            "version": runtime.version,
            "health": health.model_dump(mode="json"),
            "hardware": runtime.models.hardware(),
            "tools": [f"{spec.name}:{spec.risk.value}" for spec in runtime.tool_catalog()],
            "integrations": [view.model_dump(mode="json") for view in runtime.integrations.views()],
            "database": str(runtime.config.db_path),
            "settings": runtime.config.public_dict(),
            "remote_access": RemoteAccessManager().status(),
            "startup": StartupTaskManager().status(),
        }
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        await runtime.shutdown()


async def _benchmark_model(max_cases: int) -> int:
    runtime = MatrixRuntime.build(MatrixConfig.from_env())
    try:
        config = runtime.config
        if not runtime.models.configured:
            raise ModelUnavailable(
                "Configure a local model first. For Ollama, install it, run "
                "'ollama pull qwen2.5:0.5b-instruct-q4_K_M', then set MATRIX_MODEL_PROVIDER=ollama."
            )
        if config.model_provider == "openai" and not is_local_endpoint(config.openai_base_url):
            raise ModelUnavailable(
                "The benchmark is local-only. Configure MATRIX_OPENAI_BASE_URL to a loopback/private "
                "llama.cpp-compatible endpoint; public cloud endpoints are refused."
            )
        if not 1 <= max_cases <= len(DEFAULT_BENCHMARK_CASES):
            raise ValidationFailure(
                f"--max-cases must be between 1 and {len(DEFAULT_BENCHMARK_CASES)}."
            )
        health = await runtime.models.health()
        if not health.reachable or health.status != "working":
            raise ModelUnavailable(
                health.detail or "The configured local model is not ready; check the runtime and model name."
            )
        report = await run_model_benchmark(
            runtime.models,
            runtime.registry,
            cases=DEFAULT_BENCHMARK_CASES[:max_cases],
        )
        print(json.dumps(report, indent=2))
        return 0
    finally:
        await runtime.shutdown()


def _backup(out: str | None) -> int:
    config = MatrixConfig.from_env()
    runtime = MatrixRuntime.build(config)
    try:
        destination = Path(out) if out else config.home / "backups" / (
            f"matrix-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
        )
        path = runtime.store.backup(destination)
        import sqlite3

        check = sqlite3.connect(str(path))
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        print(f"Backup: {path}\nIntegrity: {integrity}")
        return 0 if integrity == "ok" else 1
    finally:
        asyncio.run(runtime.shutdown())


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = MatrixConfig.from_env()
    _setup_logging(config.log_level)
    try:
        if args.command in (None, "serve"):
            return _serve(args)
        if args.command == "doctor":
            return asyncio.run(_doctor())
        if args.command == "benchmark-model":
            return asyncio.run(_benchmark_model(args.max_cases))
        if args.command == "backup":
            return _backup(args.out)
        if args.command == "remote-access":
            manager = RemoteAccessManager()
            result = (
                manager.status() if args.action == "status"
                else manager.enable() if args.action == "enable"
                else manager.disable()
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "startup":
            manager = StartupTaskManager()
            result = (
                manager.status() if args.action == "status"
                else manager.install() if args.action == "install"
                else manager.uninstall()
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "pair-code":
            runtime = MatrixRuntime.build(config)
            try:
                principal = Principal(user_id="owner", scope="owner", is_owner=True)
                view = runtime.auth.issue_pairing_code(principal)
                print(f"Pairing code: {view.code}")
                print(f"Expires at:  {view.expires_at}")
                print(view.note)
                return 0
            finally:
                asyncio.run(runtime.shutdown())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except MatrixError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
