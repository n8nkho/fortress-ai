"""Auto-commit/push bounded RSI tunable and code fixes (independent of global SI auto-commit)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_TRADING_BOT = Path("/home/ubuntu/trading-bot")

RSI_TUNABLE_PARAMS = frozenset({"rsi_entry_threshold", "rsi_exit_threshold"})

# Repo-relative paths allowed for RSI-only autonomous git deploy.
_FORTRESS_RSI_PATHS = frozenset(
    {
        "utils/unified_position_exit.py",
        "utils/tunable_overrides.py",
        "data/tunable_params.json",
        "tests/test_unified_position_exit.py",
    }
)
_TRADING_BOT_RSI_PATHS = frozenset(
    {
        "utils/adaptive_rsi.py",
        "utils/adaptive_rsi_reconciliation.py",
        "utils/classic_si_screener.py",
        "data/screener_si_overrides.json",
        "tests/test_adaptive_rsi.py",
        "tests/test_adaptive_rsi_reconciliation.py",
        "tests/test_classic_si_screener.py",
    }
)


def rsi_auto_commit_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_RSI_AUTO_COMMIT", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def rsi_auto_push_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_RSI_AUTO_PUSH", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _allowlist_for_repo(repo: Path) -> frozenset[str]:
    if repo.name == "trading-bot":
        return _TRADING_BOT_RSI_PATHS
    return _FORTRESS_RSI_PATHS


def is_rsi_deploy_path(path: str, *, repo: Path | None = None) -> bool:
    norm = _normalize_rel(path)
    allow = _allowlist_for_repo(repo or _ROOT)
    if norm in allow:
        return True
    # tests touching RSI modules
    if norm.startswith("tests/") and "rsi" in norm.lower():
        return True
    return False


def all_paths_rsi_deployable(paths: list[str], *, repo: Path) -> bool:
    if not paths:
        return False
    return all(is_rsi_deploy_path(p, repo=repo) for p in paths)


def _git_diff_paths(repo: Path) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        paths = set()
        for out in (r.stdout, staged.stdout, untracked.stdout):
            for line in (out or "").splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
        return sorted(paths)
    except Exception:
        return []


def _commit_paths(repo: Path, paths: list[str], message: str) -> tuple[bool, str]:
    if not rsi_auto_commit_enabled():
        return True, "rsi_commit_disabled"
    try:
        existing = [p for p in paths if (repo / p).exists() or _normalize_rel(p) in _git_diff_paths(repo)]
        if not existing:
            return True, "nothing_to_commit"
        subprocess.run(["git", "add", "--"] + existing, cwd=repo, check=False, timeout=60)
        r = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
            return True, "nothing_to_commit"
        return r.returncode == 0, (r.stdout or r.stderr or "")[-500:]
    except Exception as e:
        return False, str(e)[:200]


def _push_repo(repo: Path) -> tuple[bool, str]:
    if not rsi_auto_push_enabled():
        return True, "rsi_push_disabled"
    branch = "main" if repo.name == "fortress-ai" else "master"
    try:
        r = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0, (r.stdout or r.stderr or "")[-500:]
    except Exception as e:
        return False, str(e)[:200]


def deploy_rsi_git_changes(
    repo: Path,
    *,
    paths: list[str] | None = None,
    message: str,
    require_e2e: bool = True,
) -> dict[str, Any]:
    """Commit (and optionally push) when all changed paths are RSI-safe."""
    if not rsi_auto_commit_enabled():
        return {"ok": True, "skipped": "rsi_commit_disabled"}

    changed = paths if paths is not None else _git_diff_paths(repo)
    if not all_paths_rsi_deployable(changed, repo=repo):
        return {"ok": True, "skipped": "non_rsi_paths", "paths": changed}

    if require_e2e:
        script = repo / "scripts" / ("e2e_verify.sh" if repo.name == "fortress-ai" else "e2e_before_deploy.sh")
        if script.is_file():
            args = [str(script), "--no-ingest"] if "e2e_verify" in script.name else [str(script), "--quick"]
            try:
                r = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=600)
                if r.returncode != 0:
                    return {
                        "ok": False,
                        "skipped": "e2e_failed",
                        "log": (r.stdout or r.stderr or "")[-800:],
                    }
            except Exception as e:
                return {"ok": False, "skipped": "e2e_error", "error": str(e)[:200]}

    c_ok, c_log = _commit_paths(repo, changed, message)
    result: dict[str, Any] = {"ok": c_ok, "commit": c_log, "paths": changed, "repo": repo.name}
    if c_ok and rsi_auto_push_enabled():
        p_ok, p_log = _push_repo(repo)
        result["push"] = p_log
        result["ok"] = c_ok and p_ok
    return result


def deploy_rsi_tunable_snapshot(*, reason: str = "rsi_tunable") -> dict[str, Any]:
    """After governance applies rsi_entry/exit threshold, commit data/tunable_params.json."""
    return deploy_rsi_git_changes(
        _ROOT,
        paths=["data/tunable_params.json"],
        message=f"chore(rsi): sync tunable_params.json ({reason})",
        require_e2e=False,
    )


def maybe_deploy_after_si_code(repo: Path, changed_paths: list[str], *, code: str, title: str) -> dict[str, Any]:
    if not all_paths_rsi_deployable(changed_paths, repo=repo):
        return {"skipped": "not_rsi_only"}
    return deploy_rsi_git_changes(
        repo,
        paths=changed_paths,
        message=f"fix(rsi): {code} — {title}",
        require_e2e=True,
    )
