"""
Autonomous code-level SI — assess, implement, verify, commit without human go.

Safety rails (always on):
- Never edit .env, immutable governance params, or weaken pre-trade gate markers
- PROTECTED_PATHS deny-list + SHA-256 integrity guard (Phase 1 hardening)
- e2e must pass before mark_implemented (configurable)
- Velocity caps per day (attempts, not only successes)
- monitor-only findings skipped unless explicit code kind
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from utils.system_time import ensure_system_tz, now_iso

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent
_TRADING_BOT = Path("/home/ubuntu/trading-bot")

FORBIDDEN_PATH_FRAGMENTS = (
    "/.env",
    "/.cursor/",
    "/data/",
    "/.vscode/",
    "/venv/",
    "__pycache__",
)

ALLOWED_WRITE_PREFIXES = (
    "agents/",
    "utils/",
    "config/",
    "scripts/",
    "tests/",
    "dashboard/",
    "deploy/",
)

# Repo-relative paths the self-coder must never modify (deny-list overrides allow-prefix).
PROTECTED_REL_PATHS = (
    "utils/pre_trade_gate.py",
    "utils/operator_halt.py",
    "agents/risk_guardian.py",
    "config/si_capability_registry.json",
    "utils/si_code_implementation.py",
    "SINGULARITY_HARDENING_PROMPT.md",
)


class RunLockBusy(Exception):
    pass


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def path_is_protected(rel_path: str) -> bool:
    norm = _normalize_rel_path(rel_path)
    for protected in PROTECTED_REL_PATHS:
        if norm == protected or norm.endswith(f"/{protected}"):
            return True
    return False


def _protected_files_for_repo(repo: Path) -> list[Path]:
    out: list[Path] = []
    for rel in PROTECTED_REL_PATHS:
        p = repo / rel
        if p.is_file():
            out.append(p)
    return out


def _snapshot_protected_files(repo: Path) -> dict[str, bytes]:
    snaps: dict[str, bytes] = {}
    for p in _protected_files_for_repo(repo):
        snaps[_normalize_rel_path(str(p.relative_to(repo)))] = p.read_bytes()
    return snaps


def _verify_protected_integrity(repo: Path, snapshots: dict[str, bytes]) -> list[str]:
    modified: list[str] = []
    for rel, expected in snapshots.items():
        p = repo / rel
        if not p.is_file():
            modified.append(rel)
            continue
        if p.read_bytes() != expected:
            modified.append(rel)
    return modified


def _restore_protected_snapshots(repo: Path, snapshots: dict[str, bytes]) -> list[str]:
    restored: list[str] = []
    for rel, data in snapshots.items():
        p = repo / rel
        if p.is_file() and p.read_bytes() != data:
            p.write_bytes(data)
            restored.append(rel)
    return restored


def _run_lock_path() -> Path:
    return _data_dir() / "si_code_implementation" / ".run.lock"


@contextmanager
def _run_lock() -> Iterator[None]:
    lock_path = _run_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fd.close()
        raise RunLockBusy("run_lock_held") from exc
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(json.dumps({"pid": os.getpid(), "started_utc": now_iso()}))
        fd.flush()
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def _si_frozen_response(context: str) -> dict[str, Any] | None:
    from utils.operator_halt import is_trading_halted

    if is_trading_halted():
        return {
            "ok": True,
            "skipped": "SI-FROZEN: trading_halted",
            "frozen": True,
            "context": context,
        }
    return None


def auto_code_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_CODE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_commit_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_COMMIT", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_push_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_PUSH", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def require_e2e() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_CODE_REQUIRE_E2E", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def max_implementations_per_day() -> int:
    try:
        return max(0, int(os.environ.get("FORTRESS_SI_AUTO_CODE_MAX_PER_DAY", "2") or 2))
    except ValueError:
        return 2


def implementation_runs_dir() -> Path:
    return _data_dir() / "si_code_implementation" / "runs"


def _cursor_bin() -> str:
    """Legacy helper — prefer _cursor_agent_argv()."""
    argv = _cursor_agent_argv("status")
    return argv[0]


def _cursor_agent_argv(prompt: str) -> list[str]:
    """
    Resolve cursor-agent invocation for headless SI runs.
    Prefers cursor-agent ( ~/.local/bin ) over cursor remote-cli subcommand.
    """
    override = (os.environ.get("FORTRESS_SI_CURSOR_BIN") or "").strip()
    home = Path.home()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    candidates.extend(
        [
            str(home / ".local/bin/cursor-agent"),
            str(home / ".local/bin/agent"),
        ]
    )
    for name in ("cursor-agent", "agent", "cursor"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    try:
        server_glob = sorted(
            (home / ".cursor-server/bin").glob("*/bin/remote-cli/cursor"),
            reverse=True,
        )
        for p in server_glob:
            candidates.append(str(p))
    except Exception:
        pass

    trust = str(os.environ.get("FORTRESS_SI_CURSOR_TRUST", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    suffix = ["--print", "--output-format", "text"]
    if trust:
        suffix.insert(0, "--trust")

    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        bin_path = str(path) if path.is_file() else (shutil.which(raw) or "")
        if not bin_path or not Path(bin_path).is_file():
            continue
        name = Path(bin_path).name
        if name in ("cursor-agent", "agent"):
            return [bin_path, *suffix, prompt]
        if name == "cursor":
            return [bin_path, "agent", *suffix, prompt]

    raise FileNotFoundError("cursor_cli_not_found")


def cursor_agent_resolved() -> dict[str, Any]:
    """Probe cursor CLI resolution for operator dashboards."""
    try:
        argv = _cursor_agent_argv("probe")
        return {"ok": True, "bin": argv[0], "mode": "cursor-agent" if Path(argv[0]).name != "cursor" else "cursor_subcommand"}
    except FileNotFoundError:
        return {"ok": False, "error": "cursor_cli_not_found"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _load_item(item_id: str) -> dict[str, Any]:
    from utils.si_recommendation_queue import load_queue

    for item in load_queue().get("items") or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    raise KeyError(f"item_not_found:{item_id}")


def _save_item(item: dict[str, Any]) -> None:
    from utils.si_recommendation_queue import load_queue, save_queue

    queue = load_queue()
    for i, row in enumerate(queue.get("items") or []):
        if isinstance(row, dict) and row.get("id") == item.get("id"):
            queue["items"][i] = item
            save_queue(queue)
            return
    raise KeyError(f"item_not_found:{item.get('id')}")


def _implementation_attempts_today() -> int:
    d = implementation_runs_dir()
    if not d.is_dir():
        return 0
    today = now_iso()[:10]
    n = 0
    for f in d.glob("*/attempt.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if str(doc.get("started_utc") or "").startswith(today):
                n += 1
        except Exception:
            continue
    return n


def _record_implementation_attempt(item_id: str) -> None:
    run_dir = implementation_runs_dir() / item_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "attempt.json").write_text(
        json.dumps({"item_id": item_id, "started_utc": now_iso()}, indent=2),
        encoding="utf-8",
    )


def _registry_meta(code: str) -> dict[str, Any]:
    from utils.si_recommendation_queue import load_fix_registry

    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code) if isinstance(reg, dict) else None
    return meta if isinstance(meta, dict) else {}


def can_auto_implement(item: dict[str, Any]) -> tuple[bool, str]:
    from utils.si_recommendation_queue import is_cross_stack_item

    if is_cross_stack_item(item):
        return False, "cross_stack_requires_human_go"
    if not auto_code_enabled():
        return False, "auto_code_disabled"
    if item.get("status") != "open":
        return False, "not_open"
    if item.get("code_implementation", {}).get("status") == "implementing":
        return False, "already_implementing"

    kind = str(item.get("kind") or "")
    if kind == "monitor":
        return False, "monitor_only"

    assessment = item.get("agent_assessment") or {}
    if assessment and not assessment.get("worth_implementing"):
        return False, "agent_dismissed"

    disp = str(item.get("disposition") or "")
    tier = str(
        item.get("governance_tier")
        or (item.get("finding") or {}).get("governance_tier")
        or ""
    ).strip()
    human_go = item.get("human_go") or {}
    if disp == "pending_human_go" and not human_go.get("approved"):
        return False, "pending_human_go_not_approved"
    if tier.startswith("tier_1") or tier in ("tier_2_human", "tier_3_immutable"):
        if not human_go.get("approved"):
            return False, f"governance_tier_requires_human_go:{tier or 'unknown'}"

    allowed_disp = {
        "auto_implement_queued",
        "pending_agent_review",
    }
    if disp not in allowed_disp and not assessment.get("worth_implementing"):
        return False, f"disposition:{disp}"

    if _implementation_attempts_today() >= max_implementations_per_day():
        return False, "daily_velocity_cap"

    meta = _registry_meta(str(item.get("code") or ""))
    if meta.get("kind") == "monitor":
        return False, "registry_monitor"

    return True, "ok"


def _heuristic_assessment(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("code") or "")
    meta = _registry_meta(code)
    kind = str(item.get("kind") or meta.get("kind") or "")
    rec = str(item.get("recommendation") or meta.get("recommendation") or "").strip()
    impact = str(item.get("impact") or meta.get("impact") or "medium")

    worth = kind in ("code_guard", "tunable", "ops") and bool(rec)
    if not worth and bool(rec) and kind in ("", "unknown"):
        worth = True
    if kind == "monitor":
        worth = False
    if code.startswith("si_capability") and "objective" in code:
        worth = False  # meta-knobs handle these

    plan = rec
    if worth and meta.get("mitigation_markers"):
        plan += f"\nAdd log markers: {', '.join(meta['mitigation_markers'])}."
    plan += "\nRegister fix in config/si_fix_registry.json when done."
    plan += "\nRun ./scripts/e2e_verify.sh --no-ingest before finishing."

    return {
        "worth_implementing": worth,
        "rationale": (
            f"Heuristic: kind={kind} impact={impact} — "
            + ("actionable code fix" if worth else "monitor or meta-only")
        ),
        "proposed_implementation": plan[:8000],
        "reviewer": "si_auto_heuristic",
    }


def _llm_assessment(item: dict[str, Any]) -> dict[str, Any] | None:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return None
    code = item.get("code")
    meta = _registry_meta(str(code or ""))
    prompt = f"""You triage a trading-system SI queue item for autonomous code implementation.

Item:
- code: {code}
- title: {item.get('title')}
- kind: {item.get('kind') or meta.get('kind')}
- impact: {item.get('impact')}
- component: {item.get('component')}
- recommendation: {item.get('recommendation') or meta.get('recommendation')}
- finding: {json.dumps(item.get('finding') or {}, default=str)[:2000]}

Rules:
- worth_implementing=true ONLY for concrete code changes in fortress-ai or trading-bot
- false for monitor-only, env secret changes, lowering risk rails, or vague ops
- NEVER propose weakening pre_trade_gate, position caps, or immutable params
- proposed_implementation: step-by-step files to edit (max 600 words)

Reply JSON only:
{{"worth_implementing": bool, "rationale": "...", "proposed_implementation": "..."}}
"""
    try:
        from agents.unified_ai_agent import call_deepseek

        text, _ = call_deepseek(prompt, max_out_tokens=900)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        doc = json.loads(m.group())
        if not isinstance(doc, dict):
            return None
        return {
            "worth_implementing": bool(doc.get("worth_implementing")),
            "rationale": str(doc.get("rationale") or "")[:4000],
            "proposed_implementation": str(doc.get("proposed_implementation") or "")[:8000],
            "reviewer": "si_auto_llm",
        }
    except Exception:
        return None


def auto_assess_item(item_id: str) -> dict[str, Any]:
    from utils.si_recommendation_queue import (
        DISPOSITION_AUTO_IMPLEMENT_QUEUED,
        DISPOSITION_PENDING_HUMAN,
        is_cross_stack_item,
    )

    item = _load_item(item_id)
    assessed = _llm_assessment(item) or _heuristic_assessment(item)
    item["agent_assessment"] = {**assessed, "assessed_utc": now_iso()}

    if is_cross_stack_item(item):
        item["disposition"] = DISPOSITION_PENDING_HUMAN
        item["requires_human_go"] = True
        item["updated_utc"] = now_iso()
        _save_item(item)
        return item

    if assessed.get("worth_implementing"):
        if auto_code_enabled():
            item["disposition"] = DISPOSITION_AUTO_IMPLEMENT_QUEUED
        else:
            item["disposition"] = DISPOSITION_PENDING_HUMAN
    else:
        item["status"] = "closed"
        item["disposition"] = "dismissed"
        item["closed_reason"] = "auto_assess_dismissed"

    item["updated_utc"] = now_iso()
    _save_item(item)
    return item


def auto_approve_enabled() -> bool:
    """When on, pending_human_go items auto-queue for implementation (no manual go)."""
    return str(os.environ.get("FORTRESS_SI_AUTO_APPROVE", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_promote_pending_human_go(*, limit: int = 5) -> list[dict[str, Any]]:
    """Promote assessed human-go items to auto_implement_queued."""
    if not auto_approve_enabled() or not auto_code_enabled():
        return []

    from utils.si_recommendation_queue import (
        DISPOSITION_AUTO_IMPLEMENT_QUEUED,
        DISPOSITION_PENDING_HUMAN,
        is_cross_stack_item,
        list_pending,
    )

    promoted: list[dict[str, Any]] = []
    for item in list_pending(disposition=DISPOSITION_PENDING_HUMAN, limit=limit):
        if is_cross_stack_item(item):
            continue
        assessment = item.get("agent_assessment") or {}
        if not assessment.get("worth_implementing"):
            if not assessment:
                try:
                    item = auto_assess_item(str(item["id"]))
                    assessment = item.get("agent_assessment") or {}
                except Exception:
                    continue
            if not assessment.get("worth_implementing"):
                continue
        item["human_go"] = {
            "approved": True,
            "note": "auto_approved",
            "decided_utc": now_iso(),
        }
        item["disposition"] = DISPOSITION_AUTO_IMPLEMENT_QUEUED
        item["implementation_ready"] = True
        item["updated_utc"] = now_iso()
        _save_item(item)
        promoted.append({"id": item.get("id"), "code": item.get("code"), "title": item.get("title")})
    return promoted


def auto_assess_pending(*, limit: int = 5) -> list[dict[str, Any]]:
    from utils.si_recommendation_queue import DISPOSITION_PENDING_AGENT, list_pending

    out: list[dict[str, Any]] = []
    for item in list_pending(disposition=DISPOSITION_PENDING_AGENT, limit=limit):
        try:
            out.append(auto_assess_item(str(item["id"])))
        except Exception as e:
            out.append({"id": item.get("id"), "error": str(e)[:120]})
    return out


def build_implementation_prompt(item: dict[str, Any]) -> str:
    assessment = item.get("agent_assessment") or {}
    meta = _registry_meta(str(item.get("code") or ""))
    plan = str(assessment.get("proposed_implementation") or item.get("recommendation") or "")
    markers = meta.get("mitigation_markers") or []

    return f"""Implement this SI fix autonomously in the Fortress stack.

## Item
- ID: {item.get('id')}
- Code: {item.get('code')}
- Title: {item.get('title')}
- Component: {item.get('component')}
- Impact: {item.get('impact')}

## Plan
{plan}

## Repos (absolute paths)
- fortress-ai: {_ROOT}
- trading-bot (Classic): {_TRADING_BOT}

## Hard constraints
- Do NOT edit .env, .cursor/, data/, or weaken pre-trade gate / immutable caps
- NEVER edit protected files: {', '.join(PROTECTED_REL_PATHS)}
- Only edit: agents/, utils/, config/, scripts/, tests/, dashboard/, deploy/ (non-protected)
- Minimize diff scope; match existing code style
- Add detectable log/block_reason markers: {', '.join(markers) if markers else 'as appropriate'}
- Update config/si_fix_registry.json for code {item.get('code')} if new mitigation
- Do NOT git commit — the SI runner commits after e2e

## Finish
Implement the fix completely, then summarize files changed and markers added.
"""


def _git_diff_paths(repo: Path) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return []
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def _diff_allowed(paths: list[str]) -> tuple[bool, str]:
    for p in paths:
        if path_is_protected(p):
            return False, f"SI-BLOCKED: protected_path:{p}"
        if any(frag in p for frag in FORBIDDEN_PATH_FRAGMENTS):
            return False, f"forbidden_path:{p}"
        if not any(p.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
            return False, f"outside_allowlist:{p}"
    return True, "ok"


def _run_e2e(repo: Path) -> tuple[bool, str]:
    script = repo / "scripts" / ("e2e_verify.sh" if repo.name == "fortress-ai" else "e2e_before_deploy.sh")
    if not script.exists():
        return True, "no_e2e_script"
    try:
        r = subprocess.run(
            [str(script), "--no-ingest"] if "e2e_verify" in script.name else [str(script), "--quick"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=600,
        )
        tail = (r.stdout or r.stderr or "")[-2000:]
        return r.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "e2e_timeout"
    except Exception as e:
        return False, str(e)[:200]


def _auto_commit(repo: Path, message: str) -> tuple[bool, str]:
    if not auto_commit_enabled():
        return True, "commit_disabled"
    try:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=False, timeout=60)
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


def _auto_push(repo: Path) -> tuple[bool, str]:
    if not auto_push_enabled():
        return True, "push_disabled"
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


def _run_cursor_agent(prompt: str, *, cwd: Path) -> tuple[int, str]:
    api_key = (os.environ.get("CURSOR_API_KEY") or os.environ.get("FORTRESS_SI_CURSOR_API_KEY") or "").strip()
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in (env.get("PATH") or "").split(":"):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    if api_key:
        env["CURSOR_API_KEY"] = api_key
    try:
        cmd = _cursor_agent_argv(prompt)
    except FileNotFoundError:
        return -1, "cursor_cli_not_found"
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("FORTRESS_SI_CODE_TIMEOUT_SEC", "2400") or 2400),
            env=env,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out[-12000:]
    except subprocess.TimeoutExpired:
        return -1, "cursor_agent_timeout"
    except FileNotFoundError:
        return -1, "cursor_cli_not_found"
    except Exception as e:
        return -1, str(e)[:500]


def implement_item(item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    from utils.si_recommendation_queue import mark_implemented

    frozen = _si_frozen_response("implement_item")
    if frozen:
        frozen["item_id"] = item_id
        return frozen

    item = _load_item(item_id)
    ok, reason = can_auto_implement(item)
    if not ok:
        return {"ok": False, "skipped": reason, "item_id": item_id}

    run_dir = implementation_runs_dir() / item_id
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_implementation_prompt(item)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    if dry_run:
        paths_probe = ["utils/pre_trade_gate.py"]
        allowed, block = _diff_allowed(paths_probe)
        return {
            "ok": True,
            "dry_run": True,
            "item_id": item_id,
            "prompt_path": str(run_dir / "prompt.md"),
            "protected_probe": {"allowed": allowed, "block": block},
        }

    try:
        with _run_lock():
            _record_implementation_attempt(item_id)

            item.setdefault("code_implementation", {})
            item["code_implementation"].update({"status": "implementing", "started_utc": now_iso()})
            item["updated_utc"] = now_iso()
            _save_item(item)

            repos = [_ROOT]
            if _TRADING_BOT.is_dir() and "classic" in str(item.get("component") or "").lower():
                repos.append(_TRADING_BOT)

            protected_snaps: dict[str, dict[str, bytes]] = {
                repo.name: _snapshot_protected_files(repo) for repo in repos
            }

            agent_outputs: list[str] = []
            for repo in repos:
                code, out = _run_cursor_agent(prompt, cwd=repo)
                agent_outputs.append(f"--- {repo.name} exit={code} ---\n{out}")
                (run_dir / f"agent_{repo.name}.txt").write_text(out, encoding="utf-8")

            for repo in repos:
                modified = _verify_protected_integrity(repo, protected_snaps.get(repo.name) or {})
                if modified:
                    _restore_protected_snapshots(repo, protected_snaps.get(repo.name) or {})
                    result = {
                        "ok": False,
                        "item_id": item_id,
                        "error": f"SI-FROZEN: protected_file_modified {','.join(modified)}",
                        "paths": modified,
                        "finished_utc": now_iso(),
                    }
                    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                    item["code_implementation"].update({"status": "frozen", **result})
                    _save_item(item)
                    return result

            changed_repos: list[str] = []
            for repo in repos:
                paths = _git_diff_paths(repo)
                if paths:
                    allowed, block = _diff_allowed(paths)
                    if not allowed:
                        result = {
                            "ok": False,
                            "item_id": item_id,
                            "error": block,
                            "paths": paths,
                            "finished_utc": now_iso(),
                        }
                        (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                        item["code_implementation"].update({"status": "blocked", **result})
                        _save_item(item)
                        return result
                    changed_repos.append(repo.name)

            e2e_ok = True
            e2e_log = ""
            if require_e2e() and changed_repos:
                for repo in repos:
                    if repo.name not in changed_repos:
                        continue
                    ok_e2e, log = _run_e2e(repo)
                    e2e_log += f"\n[{repo.name}] {log[-800:]}"
                    if not ok_e2e:
                        e2e_ok = False
                        break

            commits: dict[str, str] = {}
            pushes: dict[str, str] = {}
            if e2e_ok and changed_repos:
                msg = f"SI auto-fix: {item.get('code')} — {item.get('title')}"
                for repo in repos:
                    if repo.name not in changed_repos:
                        continue
                    c_ok, c_log = _auto_commit(repo, msg)
                    commits[repo.name] = c_log
                    if c_ok and auto_push_enabled():
                        p_ok, p_log = _auto_push(repo)
                        pushes[repo.name] = p_log

            success = e2e_ok and (bool(changed_repos) or not require_e2e())
            result = {
                "ok": success,
                "item_id": item_id,
                "code": item.get("code"),
                "changed_repos": changed_repos,
                "e2e_ok": e2e_ok,
                "commits": commits,
                "pushes": pushes,
                "agent_summary": agent_outputs[-1][-1500:] if agent_outputs else "",
                "finished_utc": now_iso(),
            }
            (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

            item["code_implementation"].update(
                {
                    "status": "completed" if success else "failed",
                    "result": result,
                    "finished_utc": now_iso(),
                }
            )
            _save_item(item)

            if success:
                mark_implemented(
                    item_id,
                    note=f"si_auto_code: repos={changed_repos} e2e={e2e_ok}",
                )
                try:
                    from utils.si_fix_deployment import sync_deployed_from_registry

                    sync_deployed_from_registry()
                except Exception:
                    pass

            return result
    except RunLockBusy:
        return {"ok": False, "skipped": "run_lock_held", "item_id": item_id}


def auto_implement_queued(*, limit: int = 1) -> list[dict[str, Any]]:
    from utils.si_recommendation_queue import DISPOSITION_AUTO_IMPLEMENT_QUEUED, list_pending

    out: list[dict[str, Any]] = []
    for item in list_pending(disposition=DISPOSITION_AUTO_IMPLEMENT_QUEUED, limit=limit):
        try:
            out.append(implement_item(str(item["id"])))
        except Exception as e:
            out.append({"ok": False, "item_id": item.get("id"), "error": str(e)[:200]})
    return out


def run_autonomous_code_si_cycle(*, assess_limit: int = 5, implement_limit: int = 1) -> dict[str, Any]:
    """Full autonomous code SI: assess pending → implement queued."""
    if not auto_code_enabled():
        return {"ok": True, "skipped": "auto_code_disabled"}

    frozen = _si_frozen_response("run_autonomous_code_si_cycle")
    if frozen:
        return frozen

    assessed = auto_assess_pending(limit=assess_limit)
    promoted = auto_promote_pending_human_go(limit=assess_limit)
    implemented = auto_implement_queued(limit=implement_limit)
    return {
        "ok": True,
        "ts": now_iso(),
        "assessed": len(assessed),
        "assessments": assessed,
        "auto_approved": len(promoted),
        "auto_approved_items": promoted,
        "implemented": len(implemented),
        "implementations": implemented,
        "cursor_cli": cursor_agent_resolved(),
        "remaining_today_cap": max(0, max_implementations_per_day() - _implementation_attempts_today()),
    }
