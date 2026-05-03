"""Tier-2 prompt evolution — validation, velocity, store (no LLM)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _reload_store(td: Path):
    os.environ["FORTRESS_AI_DATA_DIR"] = str(td)
    import importlib

    import utils.prompt_evolution_store as ps

    importlib.reload(ps)
    return ps


def _reload_pe(td: Path):
    os.environ["FORTRESS_AI_DATA_DIR"] = str(td)
    import importlib

    import utils.prompt_evolution_store as pstore

    importlib.reload(pstore)
    import agents.prompt_evolution as pe

    importlib.reload(pe)
    return pe.get_prompt_evolution(), pe, pstore


class TestPromptEvolution(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._td, ignore_errors=True)
        self._prev = os.environ.get("FORTRESS_AI_DATA_DIR")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("FORTRESS_AI_DATA_DIR", None)
        else:
            os.environ["FORTRESS_AI_DATA_DIR"] = self._prev

    def test_validate_blocks_bypass_phrase(self):
        ps = _reload_store(Path(self._td))
        ok, reason = ps.validate_appendix_text("Please bypass the gate when convenient.")
        self.assertFalse(ok)
        self.assertIn("blocked", reason)

    def test_validate_accepts_safe_appendix(self):
        ps = _reload_store(Path(self._td))
        ok, reason = ps.validate_appendix_text(
            "Prefer wait when macro volatility spikes; cite explicit invalidation for entries."
        )
        self.assertTrue(ok)

    def test_approve_pending_writes_overlay(self):
        td = Path(self._td)
        pe, _mod, pstore = _reload_pe(td)
        pstore.save_pending(
            {
                "proposal_id": "p1",
                "proposed_appendix": "Short safe guidance for testing prompt evolution store.",
                "reasoning": "t",
            }
        )
        pe.approve_pending("p1")
        self.assertTrue((td / "prompt_evolution_overlay.json").exists())
        o = json.loads((td / "prompt_evolution_overlay.json").read_text(encoding="utf-8"))
        self.assertIn("Short safe guidance", o.get("text", ""))

    def test_ab_alternates_variants(self):
        ps = _reload_store(Path(self._td))
        cfg = ps.load_config()
        from datetime import datetime, timedelta, timezone

        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        cfg["ab_test"] = {
            "active": True,
            "ends_utc": end,
            "baseline_appendix": "BASE",
            "candidate_appendix": "CAND",
        }
        ps.save_config(cfg)
        s0 = {"last_actions": []}
        s1 = {"last_actions": [{}]}
        a0, v0 = ps.get_prompt_appendix_for_cycle(s0)
        a1, v1 = ps.get_prompt_appendix_for_cycle(s1)
        self.assertEqual(v0, "A_baseline")
        self.assertEqual(v1, "B_candidate")
        self.assertEqual(a0.strip(), "BASE")
        self.assertEqual(a1.strip(), "CAND")


if __name__ == "__main__":
    unittest.main()
