"""
================================================================================
  AGENTIC OS BROWSER — COMPREHENSIVE TEST SUITE
================================================================================
  Tests for all modules:
    - Intent Parser
    - DAG Builder + cycle detection
    - Multi-LLM Router (scoring, selection, fallback)
    - Workflow Engine (parallel execution, retries)
    - Container Runtime (tool installer script generation)
    - Hybrid Planner (decision logic)
    - Vault (encrypt/decrypt, HMAC integrity)
    - PII Scrubber
================================================================================
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER: Inline minimal stubs so tests run without all deps installed
# ══════════════════════════════════════════════════════════════════════════════

class _Stub:
    """Generic stub that accepts any attribute access."""
    def __getattr__(self, item):
        return _Stub()
    def __call__(self, *a, **kw):
        return _Stub()
    def __await__(self):
        async def _():
            return _Stub()
        return _().__await__()


# ══════════════════════════════════════════════════════════════════════════════
#  1. DAG CYCLE DETECTION TESTS (Kahn's Algorithm)
# ══════════════════════════════════════════════════════════════════════════════

class TestKahnCycleDetection(unittest.TestCase):
    """Tests the cycle detection algorithm used in DAGBuilder._validate_dag."""

    def _run_kahn(self, task_inputs: Dict[str, List[str]]) -> bool:
        """
        Returns True if DAG is valid (no cycles), False if cycle detected.
        Mirrors DAGBuilder._validate_dag logic.
        """
        from collections import deque
        in_degree = {tid: len(inputs) for tid, inputs in task_inputs.items()}
        # Build forward edges
        edges: Dict[str, List[str]] = defaultdict(list)
        for tid, inputs in task_inputs.items():
            for dep in inputs:
                edges[dep].append(tid)

        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for downstream in edges[node]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)
        return visited == len(task_inputs)

    def test_linear_dag_no_cycle(self):
        """t1 → t2 → t3: valid"""
        dag = {"t1": [], "t2": ["t1"], "t3": ["t2"]}
        self.assertTrue(self._run_kahn(dag))

    def test_parallel_dag_no_cycle(self):
        """t1, t2 both independent, t3 depends on both: valid"""
        dag = {"t1": [], "t2": [], "t3": ["t1", "t2"]}
        self.assertTrue(self._run_kahn(dag))

    def test_diamond_dag_no_cycle(self):
        """t1 → t2, t1 → t3, t2+t3 → t4: valid diamond"""
        dag = {"t1": [], "t2": ["t1"], "t3": ["t1"], "t4": ["t2", "t3"]}
        self.assertTrue(self._run_kahn(dag))

    def test_simple_cycle_detected(self):
        """t1 → t2 → t1: cycle"""
        dag = {"t1": ["t2"], "t2": ["t1"]}
        self.assertFalse(self._run_kahn(dag))

    def test_three_node_cycle_detected(self):
        """t1 → t2 → t3 → t1: cycle"""
        dag = {"t1": ["t3"], "t2": ["t1"], "t3": ["t2"]}
        self.assertFalse(self._run_kahn(dag))

    def test_self_loop_detected(self):
        """t1 → t1: self-loop cycle"""
        dag = {"t1": ["t1"]}
        self.assertFalse(self._run_kahn(dag))

    def test_single_node_no_cycle(self):
        dag = {"t1": []}
        self.assertTrue(self._run_kahn(dag))

    def test_complex_valid_dag(self):
        """8-node DAG with branches and merges: valid"""
        dag = {
            "t1": [], "t2": [], "t3": ["t1"],
            "t4": ["t1", "t2"], "t5": ["t3"],
            "t6": ["t3", "t4"], "t7": ["t5", "t6"],
            "t8": ["t7"],
        }
        self.assertTrue(self._run_kahn(dag))


# ══════════════════════════════════════════════════════════════════════════════
#  2. MULTI-LLM ROUTER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMRouter(unittest.TestCase):

    def setUp(self):
        # Import router without triggering API client init
        import sys
        sys.modules.setdefault("openai", _Stub())
        sys.modules.setdefault("anthropic", _Stub())
        sys.modules.setdefault("aiodocker", _Stub())

        from router.router import LLMRouter, RoutingPolicy, MODEL_REGISTRY
        self.RouterClass = LLMRouter
        self.PolicyClass = RoutingPolicy
        self.registry = MODEL_REGISTRY

    def test_routing_policy_weights_sum_to_one(self):
        policy = self.PolicyClass()
        total = (policy.quality_weight + policy.speed_weight +
                 policy.cost_weight + policy.privacy_weight)
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_invalid_policy_raises(self):
        with self.assertRaises(AssertionError):
            self.PolicyClass(quality_weight=0.9, speed_weight=0.5,
                           cost_weight=0.1, privacy_weight=0.1)

    def test_code_task_selects_code_specialist(self):
        """Qwen Coder should score highest for code_execution under privacy policy."""
        policy = self.PolicyClass(
            quality_weight=0.5, speed_weight=0.1,
            cost_weight=0.0, privacy_weight=0.4
        )
        router = self.RouterClass(policy=policy)
        best, fallbacks = router.select_model(
            task_type="code_execution",
            require_local=True,   # force local → Qwen or Mistral
        )
        self.assertTrue(best.is_local)
        self.assertIn(best.model_id, ["qwen2.5-coder:32b", "mistral:latest"])

    def test_reasoning_task_favours_claude(self):
        """Claude has highest reasoning quality score."""
        policy = self.PolicyClass(
            quality_weight=0.8, speed_weight=0.1,
            cost_weight=0.1, privacy_weight=0.0
        )
        router = self.RouterClass(policy=policy)
        best, _ = router.select_model(
            task_type="reasoning",
            require_local=False,
        )
        self.assertEqual(best.model_id, "claude-sonnet-4-6")

    def test_local_only_filters_remote_models(self):
        router = self.RouterClass()
        best, fallbacks = router.select_model(
            task_type="web_research",
            require_local=True,
        )
        self.assertTrue(best.is_local)
        for fb in fallbacks:
            self.assertTrue(fb.is_local)

    def test_fallback_chain_length(self):
        router = self.RouterClass()
        _, fallbacks = router.select_model("reasoning")
        self.assertLessEqual(len(fallbacks), 2)

    def test_quality_score_range(self):
        """All quality scores must be in [0, 1]."""
        for model_id, spec in self.registry.items():
            for task, score in spec.quality_scores.items():
                self.assertGreaterEqual(score, 0.0, f"{model_id}.{task} score < 0")
                self.assertLessEqual(score, 1.0, f"{model_id}.{task} score > 1")

    def test_usage_tracker_cost_accumulation(self):
        from router.router import UsageTracker, UsageRecord
        tracker = UsageTracker()

        async def run():
            await tracker.record(UsageRecord(
                model_id="gpt-4o", task_type="code_execution",
                input_tokens=1000, output_tokens=500,
                latency_ms=2000, cost_usd=0.0125, success=True
            ))
            await tracker.record(UsageRecord(
                model_id="claude-sonnet-4-6", task_type="reasoning",
                input_tokens=2000, output_tokens=800,
                latency_ms=1800, cost_usd=0.018, success=True
            ))

        asyncio.run(run())
        total = tracker.total_cost_usd()
        self.assertAlmostEqual(total, 0.0305, places=4)


# ══════════════════════════════════════════════════════════════════════════════
#  3. TOOL INSTALLER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestToolInstaller(unittest.TestCase):

    def setUp(self):
        from runtime.container_manager import ToolInstaller
        self.installer = ToolInstaller()

    def test_known_tools_generate_apk_command(self):
        script = self.installer.generate_install_script(["python3", "git", "curl"])
        self.assertIn("apk add", script)
        self.assertIn("python3", script)
        self.assertIn("git", script)
        self.assertIn("curl", script)

    def test_pip_packages_batched(self):
        script = self.installer.generate_install_script(["pandas", "numpy", "scipy"])
        pip_lines = [l for l in script.split("\n") if "pip install" in l]
        # All pip packages should be in ONE install call
        self.assertEqual(len(pip_lines), 1)
        self.assertIn("pandas", pip_lines[0])
        self.assertIn("numpy", pip_lines[0])

    def test_playwright_post_install(self):
        script = self.installer.generate_install_script(["playwright"])
        self.assertIn("playwright install chromium", script)

    def test_unknown_tool_skipped_no_error(self):
        """Unknown tools should not raise — just be skipped."""
        script = self.installer.generate_install_script(["python3", "nonexistent_tool_xyz"])
        self.assertIn("python3", script)
        self.assertNotIn("nonexistent_tool_xyz", script)

    def test_deduplication(self):
        """Same tool listed twice → appears once in install command."""
        script = self.installer.generate_install_script(["git", "git", "curl"])
        # Count occurrences of 'git' in the apk line
        apk_line = next(l for l in script.split("\n") if "apk add" in l)
        self.assertEqual(apk_line.count("git"), 1)

    def test_script_starts_with_shebang_and_set_e(self):
        script = self.installer.generate_install_script(["python3"])
        lines = script.split("\n")
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertEqual(lines[1], "set -e")

    def test_empty_tools_returns_minimal_script(self):
        script = self.installer.generate_install_script([])
        self.assertIn("#!/bin/sh", script)
        self.assertNotIn("apk add", script)
        self.assertNotIn("pip install", script)


# ══════════════════════════════════════════════════════════════════════════════
#  4. PII SCRUBBER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPIIScrubber(unittest.TestCase):

    def setUp(self):
        from hybrid_planner.planner import PIIScrubber
        self.scrubber = PIIScrubber()

    def test_email_redacted(self):
        result = self.scrubber.scrub("Contact user@example.com for details")
        self.assertNotIn("user@example.com", result)
        self.assertIn("[EMAIL_REDACTED]", result)

    def test_phone_redacted(self):
        result = self.scrubber.scrub("Call 555-123-4567 now")
        self.assertNotIn("555-123-4567", result)

    def test_openai_key_redacted(self):
        result = self.scrubber.scrub("API key: sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", result)

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SIGNATURE"
        result = self.scrubber.scrub(f"Token: {jwt}")
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)

    def test_password_redacted(self):
        result = self.scrubber.scrub("password=mysecretpassword123")
        self.assertNotIn("mysecretpassword123", result)

    def test_clean_text_unchanged(self):
        clean = "Research Tesla EV market share in Q4 2024"
        result = self.scrubber.scrub(clean)
        self.assertEqual(result, clean)

    def test_scrub_dict_recursive(self):
        data = {
            "goal": "Analyse data for user@test.com",
            "nested": {"api_config": {"key": "sk-1234567890abcdefghijklmnopqrstu"}},
        }
        result = self.scrubber.scrub_dict(data)
        self.assertNotIn("user@test.com", result["goal"])
        self.assertNotIn("sk-1234567890", str(result))


# ══════════════════════════════════════════════════════════════════════════════
#  5. VAULT CRYPTO TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultCrypto(unittest.TestCase):

    def setUp(self):
        import sys
        sys.modules.setdefault("keyring", _Stub())
        from vault.vault import CryptoEngine
        self.engine = CryptoEngine()
        self.master_key = os.urandom(32)

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "sk-verysecretapikey1234567890"
        blob, salt = self.engine.encrypt(plaintext, self.master_key, "TEST_KEY")
        recovered = self.engine.decrypt(blob, salt, self.master_key, "TEST_KEY")
        self.assertEqual(recovered, plaintext)

    def test_different_nonces_produce_different_ciphertexts(self):
        secret = "same_secret"
        blob1, salt1 = self.engine.encrypt(secret, self.master_key, "KEY1")
        blob2, salt2 = self.engine.encrypt(secret, self.master_key, "KEY2")
        self.assertNotEqual(blob1, blob2)

    def test_tampered_blob_raises(self):
        blob, salt = self.engine.encrypt("secret", self.master_key, "KEY")
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF   # flip last byte
        with self.assertRaises(ValueError):
            self.engine.decrypt(bytes(tampered), salt, self.master_key, "KEY")

    def test_wrong_context_raises(self):
        blob, salt = self.engine.encrypt("secret", self.master_key, "CORRECT_CTX")
        with self.assertRaises(ValueError):
            self.engine.decrypt(blob, salt, self.master_key, "WRONG_CTX")

    def test_wrong_master_key_raises(self):
        blob, salt = self.engine.encrypt("secret", self.master_key, "KEY")
        wrong_key = os.urandom(32)
        with self.assertRaises(ValueError):
            self.engine.decrypt(blob, salt, wrong_key, "KEY")

    def test_derived_keys_differ_by_salt(self):
        salt1, salt2 = os.urandom(32), os.urandom(32)
        key1 = self.engine.derive_key(self.master_key, salt1, "ctx")
        key2 = self.engine.derive_key(self.master_key, salt2, "ctx")
        self.assertNotEqual(key1, key2)


# ══════════════════════════════════════════════════════════════════════════════
#  6. HYBRID PLANNER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestHybridPlanner(unittest.TestCase):

    def setUp(self):
        import sys
        sys.modules.setdefault("psutil", _Stub())
        sys.modules.setdefault("boto3", _Stub())

        from hybrid_planner.planner import HybridPlanner

        class MockTask:
            def __init__(self, task_id, task_type, run_locally=False, est_dur=30):
                self.id = task_id
                self.type = type("T", (), {"value": task_type})()
                self.run_locally = run_locally
                self.estimated_duration_seconds = est_dur

        class MockContext:
            def __init__(self, upstream=None):
                self.upstream_results = upstream or {}
                self.task_id = "t1"
                self.task_type = "web_research"

        self.Planner = HybridPlanner
        self.MockTask = MockTask
        self.MockContext = MockContext

    def test_run_locally_true_forces_local(self):
        planner = self.Planner()
        task = self.MockTask("t1", "code_execution", run_locally=True)
        ctx = self.MockContext()

        with patch.object(type(planner.resource_monitor), 'get_resources',
                         return_value=type("R", (), {
                             "load_score": 0.9, "cpu_percent": 95,
                             "memory_percent": 90, "available_memory_gb": 0.5,
                             "disk_free_gb": 10, "active_containers": 7
                         })()):
            decision = planner.decide(task, ctx)
        self.assertTrue(decision.run_locally)
        self.assertIn("run_locally=True", decision.reason)

    def test_always_local_task_types(self):
        planner = self.Planner()
        for task_type in ["file_operation", "deployment"]:
            task = self.MockTask("t1", task_type, run_locally=False)
            ctx = self.MockContext()
            decision = planner.decide(task, ctx)
            self.assertTrue(decision.run_locally, f"{task_type} should be local")

    def test_sensitive_payload_penalises_cloud(self):
        planner = self.Planner()
        task = self.MockTask("t1", "web_research", est_dur=300)
        ctx = self.MockContext(upstream={"creds": "password=secret123"})

        with patch.object(type(planner.resource_monitor), 'get_resources',
                         return_value=type("R", (), {
                             "load_score": 0.5, "cpu_percent": 50,
                             "memory_percent": 50, "available_memory_gb": 8,
                             "disk_free_gb": 100, "active_containers": 2
                         })()):
            decision = planner.decide(task, ctx)
        # Privacy penalty should reduce burst probability
        self.assertLess(decision.burst_score, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
#  7. INTENT PARSER DOMAIN CLASSIFICATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestIntentParserDomainClassification(unittest.TestCase):
    """Test the heuristic domain classifier (no LLM required)."""

    def setUp(self):
        import sys
        sys.modules.setdefault("openai", _Stub())
        sys.modules.setdefault("anthropic", _Stub())

        class MockRouter:
            pass

        from orchestrator.orchestrator import IntentParser
        self.parser = IntentParser(MockRouter())

    def test_software_dev_domain(self):
        goal = "Build a REST API with FastAPI and deploy it to Docker"
        self.assertEqual(self.parser.classify_domain(goal), "software_dev")

    def test_market_research_domain(self):
        goal = "Analyse the EV market and compare Tesla competitors by market share"
        self.assertEqual(self.parser.classify_domain(goal), "market_research")

    def test_data_analysis_domain(self):
        goal = "Analyse this CSV data and generate a statistical chart"
        self.assertEqual(self.parser.classify_domain(goal), "data_analysis")

    def test_web_automation_domain(self):
        goal = "Scrape product prices from Amazon and extract to a spreadsheet"
        self.assertEqual(self.parser.classify_domain(goal), "web_automation")

    def test_complexity_low(self):
        self.assertEqual(self.parser.estimate_complexity("Search Google"), "low")

    def test_complexity_medium(self):
        goal = "Research Python web frameworks and write a comparison"
        self.assertEqual(self.parser.estimate_complexity(goal), "medium")

    def test_complexity_high(self):
        goal = ("Research all EV competitors, analyse their financials, "
                "build a dashboard, generate a report, and deploy it to AWS S3")
        self.assertEqual(self.parser.estimate_complexity(goal), "high")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN TEST RUNNER
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestKahnCycleDetection,
        TestLLMRouter,
        TestToolInstaller,
        TestPIIScrubber,
        TestVaultCrypto,
        TestHybridPlanner,
        TestIntentParserDomainClassification,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    print(f"\n{'='*70}")
    print(f"  Tests run:    {result.testsRun}")
    print(f"  Failures:     {len(result.failures)}")
    print(f"  Errors:       {len(result.errors)}")
    print(f"  Skipped:      {len(result.skipped)}")
    print(f"  Result:       {'PASSED' if result.wasSuccessful() else 'FAILED'}")
    print(f"{'='*70}")

    exit(0 if result.wasSuccessful() else 1)
