"""
Autonomous code-level SI — assess, implement, verify, commit without human go.

Safety rails (always on):
- Never edit .env, immutable governance params, or weaken pre-trade gate markers
- e2e must pass before mark_implemented (configurable)
- Velocity caps per day / week
- monitor-only findings skipped unless explicit code kind
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

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


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def auto_code_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_CODE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_commit_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_COMMIT", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_push_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_AUTO_PUSH", "1")).strip().lower() in (
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
        return max(0, int(os.environ.get("FORTRESS_SI_AUTO_CODE_MAX_PER_DAY", "3") or 3))
    except ValueError:
        return 3


def implementation_runs_dir() -> Path:
    return _data_dir() / "si_code_implementation" / "runs"


def _cursor_bin() -> str:
    return shutil.which("cursor") or "cursor"


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


def _implementations_today() -> int:
    d = implementation_runs_dir()
    if not d.is_dir():
        return 0
    today = now_iso()[:10]
    n = 0
    for f in d.glob("*/result.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if str(doc.get("finished_utc") or "").startswith(today) and doc.get("ok"):
                n += 1
        except Exception:
            continue
    return n


def _registry_meta(code: str) -> dict[str, Any]:
    from utils.si_recommendation_queue import load_fix_registry

    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code) if isinstance(reg, dict) else None
    return meta if isinstance(meta, dict) else {}


def can_auto_implement(item: dict[str, Any]) -> tuple[bool, str]:
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
    allowed_disp = {
        "auto_implement_queued",
        "pending_human_go",
        "pending_agent_review",
    }
    if disp not in allowed_disp and not assessment.get("worth_implementing"):
        return False, f"disposition:{disp}"

    if _implementations_today() >= max_implementations_per_day():
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
    from utils.si_recommendation_queue import DISPOSITION_AUTO_IMPLEMENT_QUEUED, DISPOSITION_PENDING_HUMAN

    item = _load_item(item_id)
    assessed = _llm_assessment(item) or _heuristic_assessment(item)
    item["agent_assessment"] = {**assessed, "assessed_utc": now_iso()}

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
- Only edit: agents/, utils/, config/, scripts/, tests/, dashboard/, deploy/
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
    if api_key:
        env["CURSOR_API_KEY"] = api_key
    cmd = [
        _cursor_bin(),
        "agent",
        "--print",
        "--output-format",
        "text",
        prompt,
    ]
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

    item = _load_item(item_id)
    ok, reason = can_auto_implement(item)
    if not ok:
        return {"ok": False, "skipped": reason, "item_id": item_id}

    run_dir = implementation_runs_dir() / item_id
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_implementation_prompt(item)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    if dry_run:
        return {"ok": True, "dry_run": True, "item_id": item_id, "prompt_path": str(run_dir / "prompt.md")}

    item.setdefault("code_implementation", {})
    item["code_implementation"].update({"status": "implementing", "started_utc": now_iso()})
    item["updated_utc"] = now_iso()
    _save_item(item)

    repos = [_ROOT]
    if _TRADING_BOT.is_dir() and "classic" in str(item.get("component") or "").lower():
        repos.append(_TRADING_BOT)

    agent_outputs: list[str] = []
    for repo in repos:
        code, out = _run_cursor_agent(prompt, cwd=repo)
        agent_outputs.append(f"--- {repo.name} exit={code} ---\n{out}")
        (run_dir / f"agent_{repo.name}.txt").write_text(out, encoding="utf-8")

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

    assessed = auto_assess_pending(limit=assess_limit)
    implemented = auto_implement_queued(limit=implement_limit)
    return {
        "ok": True,
        "ts": now_iso(),
        "assessed": len(assessed),
        "assessments": assessed,
        "implemented": len(implemented),
        "implementations": implemented,
        "remaining_today_cap": max(0, max_implementations_per_day() - _implementations_today()),
    }
