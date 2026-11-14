"""VPS-free, GitHub-native cloud execution and state persistence.

Enables MATRIX AI to operate autonomously on GitHub Actions scheduled runs,
workflow dispatch, and repository dispatch without permanent VPS hosting,
storing durable state on an orphan Git branch (`matrix-data`) and handling
batch Telegram synchronization.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .core.models import iso
from .runtime import MatrixRuntime

log = logging.getLogger("matrix.cloud_native")

DEFAULT_STATE_BRANCH = "matrix-data"
DEFAULT_STATE_DB_RELPATH = "data/matrix.db"


class GitStateSyncError(Exception):
    """Raised when Git state synchronization fails."""


class GitStateSync:
    """Manages durable state persistence across ephemeral GitHub Actions runners.

    Uses an orphan Git branch (default: `matrix-data`) as the storage backend.
    Operations use isolated Git plumbing (GIT_INDEX_FILE, hash-object, write-tree,
    commit-tree) so the current working tree and checked-out branch are never modified.
    """

    def __init__(self, repo_dir: Path | None = None, branch: str = DEFAULT_STATE_BRANCH) -> None:
        self.repo_dir = Path(repo_dir or Path.cwd()).resolve()
        self.branch = branch

    def _run_git(self, args: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(self.repo_dir),
                check=check,
                capture_output=True,
                text=True,
                env=cmd_env,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else ""
            log.warning("git %s failed: %s", " ".join(args[:4]), stderr)
            raise GitStateSyncError(f"Git command failed: git {' '.join(args[:3])} -> {stderr}") from exc

    def check_remote_branch_exists(self, remote: str = "origin") -> bool:
        """Check if the state branch exists on the remote repository."""
        proc = self._run_git(["ls-remote", "--heads", remote, self.branch], check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    @staticmethod
    def verify_integrity(db_path: Path) -> str:
        """Run PRAGMA integrity_check on the SQLite database."""
        if not db_path.exists() or db_path.stat().st_size == 0:
            return "empty"
        try:
            con = sqlite3.connect(str(db_path))
            try:
                row = con.execute("PRAGMA integrity_check").fetchone()
                return str(row[0]) if row else "unknown"
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            log.error("SQLite integrity check failed for %s: %s", db_path, exc)
            return f"failed: {type(exc).__name__}"

    def pull_state(self, target_db_path: Path, remote: str = "origin") -> bool:
        """Fetch the latest state from the remote branch and extract the SQLite DB.

        Returns True if state was restored and passed integrity check, False otherwise.
        """
        target_db_path = Path(target_db_path).resolve()
        target_db_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.check_remote_branch_exists(remote):
            log.info("State branch '%s' does not exist on %s yet. Starting with clean state.", self.branch, remote)
            return False

        try:
            self._run_git(["fetch", remote, f"refs/heads/{self.branch}:refs/remotes/{remote}/{self.branch}"])
        except GitStateSyncError:
            # Fallback to direct fetch
            try:
                self._run_git(["fetch", remote, self.branch])
            except GitStateSyncError as exc:
                log.warning("Could not fetch branch %s from %s: %s", self.branch, remote, exc)
                return False

        # Extract data/matrix.db directly using git show
        source_ref = f"{remote}/{self.branch}:{DEFAULT_STATE_DB_RELPATH}"
        try:
            proc = subprocess.run(
                ["git", "show", source_ref],
                cwd=str(self.repo_dir),
                check=True,
                capture_output=True,
            )
            data_bytes = proc.stdout
        except subprocess.CalledProcessError:
            log.info("File %s not found in branch %s.", DEFAULT_STATE_DB_RELPATH, self.branch)
            return False

        if not data_bytes:
            log.warning("Fetched state file from %s is empty.", source_ref)
            return False

        # Write to temporary file first and verify integrity before replacing target
        temp_path = target_db_path.with_suffix(".tmp_pull")
        try:
            temp_path.write_bytes(data_bytes)
            integrity = self.verify_integrity(temp_path)
            if integrity != "ok":
                log.error("Fetched DB failed integrity check: %s. Aborting restore.", integrity)
                return False

            temp_path.replace(target_db_path)
            log.info("Successfully restored state from %s (%d bytes). Integrity: ok.", source_ref, len(data_bytes))
            return True
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def push_state(
        self,
        source_db_path: Path,
        remote: str = "origin",
        commit_message: str = "chore(data): persist matrix cloud state [skip ci]",
        max_retries: int = 3,
    ) -> bool:
        """Commit source_db_path into the orphan state branch and push to remote.

        Uses plumbing commands and a temporary index file. Retries on concurrent non-fast-forward push.
        """
        source_db_path = Path(source_db_path).resolve()
        if not source_db_path.exists() or source_db_path.stat().st_size == 0:
            log.warning("Cannot push empty or missing DB: %s", source_db_path)
            return False

        # Checkpoint WAL before reading
        try:
            con = sqlite3.connect(str(source_db_path))
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            log.warning("WAL checkpoint failed before state push: %s", exc)

        integrity = self.verify_integrity(source_db_path)
        if integrity != "ok":
            raise GitStateSyncError(f"Refusing to push database with failed integrity check: {integrity}")

        # 1. Create Git blob for the DB file
        proc = self._run_git(["hash-object", "-w", str(source_db_path)])
        blob_sha = proc.stdout.strip()
        if not blob_sha:
            raise GitStateSyncError("git hash-object produced empty blob SHA")

        git_dir = Path(self._run_git(["rev-parse", "--git-dir"]).stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (self.repo_dir / git_dir).resolve()

        temp_index = git_dir / f"idx_state_{int(time.time() * 1000)}"

        for attempt in range(1, max_retries + 1):
            try:
                temp_index.unlink(missing_ok=True)
                env = {"GIT_INDEX_FILE": str(temp_index)}

                # Check if parent commit exists locally or on remote
                parent_sha: str | None = None
                p_check = self._run_git(["rev-parse", "--verify", f"refs/heads/{self.branch}"], check=False)
                if p_check.returncode == 0 and p_check.stdout.strip():
                    parent_sha = p_check.stdout.strip()
                else:
                    p_remote = self._run_git(["rev-parse", "--verify", f"refs/remotes/{remote}/{self.branch}"], check=False)
                    if p_remote.returncode == 0 and p_remote.stdout.strip():
                        parent_sha = p_remote.stdout.strip()

                if parent_sha:
                    self._run_git(["read-tree", parent_sha], env=env)

                self._run_git(
                    ["update-index", "--add", "--cacheinfo", "100644", blob_sha, DEFAULT_STATE_DB_RELPATH],
                    env=env,
                )

                tree_proc = self._run_git(["write-tree"], env=env)
                tree_sha = tree_proc.stdout.strip()

                commit_args = ["commit-tree", tree_sha, "-m", commit_message]
                if parent_sha:
                    commit_args.extend(["-p", parent_sha])

                commit_proc = self._run_git(commit_args)
                commit_sha = commit_proc.stdout.strip()

                self._run_git(["update-ref", f"refs/heads/{self.branch}", commit_sha])

                # Push to remote
                push_proc = self._run_git(
                    ["push", remote, f"refs/heads/{self.branch}:refs/heads/{self.branch}"],
                    check=False,
                )
                if push_proc.returncode == 0:
                    log.info("Successfully pushed state to %s/%s at commit %s", remote, self.branch, commit_sha[:8])
                    return True

                log.warning("State push rejected on attempt %d: %s. Fetching and retrying...", attempt, push_proc.stderr)
                time.sleep(1.0 * attempt)
                self._run_git(["fetch", remote, f"refs/heads/{self.branch}:refs/remotes/{remote}/{self.branch}"], check=False)
            finally:
                temp_index.unlink(missing_ok=True)

        raise GitStateSyncError(f"Failed to push state branch '{self.branch}' to '{remote}' after {max_retries} attempts.")


@dataclass
class CloudStepResult:
    """Detailed telemetry and audit record of a single autonomous cloud execution step."""

    started_at: str
    finished_at: str
    duration_seconds: float
    recovered_tasks: int
    telegram_updates_processed: int
    tasks_completed: int
    reports_flushed: int
    state_pulled: bool
    state_pushed: bool
    primary_provider: str
    primary_status: str
    fallback_provider: str
    fallback_status: str
    active_tasks_remaining: int
    db_integrity: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary_markdown(self) -> str:
        """GitHub Step Summary friendly markdown report."""
        status_icon = "✅" if not self.error and self.db_integrity == "ok" else "⚠️"
        lines = [
            f"## {status_icon} MATRIX AI Cloud Execution Step",
            "",
            f"- **Execution Time:** `{self.started_at}` → `{self.finished_at}` ({self.duration_seconds:.2f}s)",
            f"- **State Persistence:** Pulled: `{'yes' if self.state_pulled else 'fresh/none'}` | Pushed: `{'yes' if self.state_pushed else 'no'}`",
            f"- **Database Integrity:** `{self.db_integrity}`",
            f"- **Tasks Recovered on Startup:** `{self.recovered_tasks}`",
            f"- **Telegram Updates Processed:** `{self.telegram_updates_processed}`",
            f"- **Tasks Completed in Run:** `{self.tasks_completed}`",
            f"- **Remaining Active Tasks:** `{self.active_tasks_remaining}`",
            f"- **Primary AI Model:** `{self.primary_provider}` (`{self.primary_status}`)",
            f"- **Fallback AI Model:** `{self.fallback_provider}` (`{self.fallback_status}`)",
        ]
        if self.error:
            lines.extend(["", "### ⚠️ Errors", f"```\n{self.error}\n```"])
        return "\n".join(lines)


async def run_cloud_step(
    *,
    config: MatrixConfig | None = None,
    repo_dir: Path | None = None,
    state_branch: str = DEFAULT_STATE_BRANCH,
    pull_state_enabled: bool = True,
    push_state_enabled: bool = True,
    sync_telegram_enabled: bool = True,
    max_tasks: int = 20,
    drain_timeout: float = 45.0,
) -> CloudStepResult:
    """Execute a single-shot, autonomous cloud execution cycle.

    Designed for GitHub Actions and serverless dispatch execution where runners
    are ephemeral and bounded by timeout limits.
    """
    started = time.time()
    started_iso = iso(datetime.fromtimestamp(started, UTC))
    config = config or MatrixConfig.from_env()

    sync = GitStateSync(repo_dir=repo_dir, branch=state_branch)
    pulled = False
    pushed = False
    recovered_count = 0
    telegram_processed = 0
    tasks_completed = 0
    flushed_reports = 0
    step_error: str | None = None

    # Step 1: Pull state if enabled
    if pull_state_enabled and config.db_path:
        try:
            pulled = sync.pull_state(config.db_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Pull state failed (proceeding with local DB): %s", exc)

    # Step 2: Build runtime
    runtime = MatrixRuntime.build(config)
    try:
        # Step 3: Run restart recovery on persisted tasks
        recovery = await runtime.orchestrator.recover_after_restart()
        recovered_count = recovery.get("resumed", 0)

        # Step 4: Process batch Telegram updates if enabled
        if sync_telegram_enabled and runtime.telegram.enabled:
            try:
                telegram_processed = await runtime.telegram.sync_once(max_updates=max_tasks)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram sync_once error: %s", exc)
                step_error = f"Telegram sync error: {exc}"

        # Step 5: Drain pending orchestrator tasks within bounded time
        tasks_completed = await runtime.orchestrator.drain(timeout=drain_timeout)

        # Step 6: Flush any pending Telegram reports
        if sync_telegram_enabled and runtime.telegram.enabled:
            try:
                flushed_reports = await runtime.telegram.flush_reports(timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram flush_reports error: %s", exc)

        # Telemetry metrics
        primary_health = await runtime.models.provider.health()
        fallback_health = (
            await runtime.models.fallback_provider.health()
            if runtime.models.fallback_provider is not None
            else None
        )
        primary_provider = runtime.models.provider.provider_name
        primary_status = primary_health.status
        fallback_provider = (
            runtime.models.fallback_provider.provider_name
            if runtime.models.fallback_provider
            else "none"
        )
        fallback_status = fallback_health.status if fallback_health else "unconfigured"

        remaining_active = len(runtime.store.nonterminal_tasks())
    finally:
        # Step 7: Clean shutdown of all services
        await runtime.shutdown()

    # Step 8: Verify database integrity and push state if enabled
    db_integrity = GitStateSync.verify_integrity(config.db_path) if config.db_path else "missing"
    if push_state_enabled and config.db_path and config.db_path.exists():
        try:
            pushed = sync.push_state(config.db_path)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to push state branch: %s", exc)
            step_error = f"{step_error}; Push state error: {exc}" if step_error else f"Push state error: {exc}"

    finished = time.time()
    finished_iso = iso(datetime.fromtimestamp(finished, UTC))

    result = CloudStepResult(
        started_at=started_iso,
        finished_at=finished_iso,
        duration_seconds=round(finished - started, 3),
        recovered_tasks=recovered_count,
        telegram_updates_processed=telegram_processed,
        tasks_completed=tasks_completed,
        reports_flushed=flushed_reports,
        state_pulled=pulled,
        state_pushed=pushed,
        primary_provider=primary_provider,
        primary_status=primary_status,
        fallback_provider=fallback_provider,
        fallback_status=fallback_status,
        active_tasks_remaining=remaining_active,
        db_integrity=db_integrity,
        error=step_error,
    )
    return result


__all__ = [
    "DEFAULT_STATE_BRANCH",
    "DEFAULT_STATE_DB_RELPATH",
    "GitStateSync",
    "GitStateSyncError",
    "CloudStepResult",
    "run_cloud_step",
]
