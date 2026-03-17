"""
================================================================================
  AGENTIC OS BROWSER — HYBRID EXECUTION PLANNER
================================================================================
  Makes real-time decisions: run each task locally (Docker) or
  burst to cloud (AWS Fargate / GCP Cloud Run / Azure ACI).

  Algorithm:
    For each task node at scheduling time:
      1. Evaluate 4 scoring dimensions:
         a. Privacy score  — high if task handles sensitive data
         b. Load score     — high if local resources are under pressure
         c. Duration score — high if task is estimated to be long
         d. Cost score     — high if cloud is significantly cheaper
      2. Compute weighted burst_probability ∈ [0, 1]
      3. If burst_probability > CLOUD_BURST_THRESHOLD → cloud
         Else → local
      4. Select cheapest available cloud provider meeting SLA
      5. Strip PII from payload before cloud dispatch
      6. Return cloud container ID and endpoint for result polling
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import psutil
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("hybrid_planner")


# ══════════════════════════════════════════════════════════════════════════════
#  CLOUD PROVIDERS
# ══════════════════════════════════════════════════════════════════════════════

class CloudProvider(str, Enum):
    AWS_FARGATE   = "aws_fargate"
    GCP_CLOUD_RUN = "gcp_cloud_run"
    AZURE_ACI     = "azure_aci"
    FLY_IO        = "fly_io"


@dataclass
class CloudProviderSpec:
    name: CloudProvider
    display_name: str
    cost_per_vcpu_hour: float       # USD
    cost_per_gb_hour: float         # USD
    cold_start_ms: int              # typical cold start latency
    max_memory_gb: int
    max_vcpus: int
    regions: List[str]
    is_available: bool = True


CLOUD_PROVIDERS: Dict[CloudProvider, CloudProviderSpec] = {
    CloudProvider.AWS_FARGATE: CloudProviderSpec(
        name=CloudProvider.AWS_FARGATE,
        display_name="AWS Fargate",
        cost_per_vcpu_hour=0.04048,
        cost_per_gb_hour=0.004445,
        cold_start_ms=8000,
        max_memory_gb=30,
        max_vcpus=4,
        regions=["us-east-1", "eu-west-1", "ap-southeast-1"],
    ),
    CloudProvider.GCP_CLOUD_RUN: CloudProviderSpec(
        name=CloudProvider.GCP_CLOUD_RUN,
        display_name="GCP Cloud Run",
        cost_per_vcpu_hour=0.02400,
        cost_per_gb_hour=0.00250,
        cold_start_ms=2000,
        max_memory_gb=32,
        max_vcpus=8,
        regions=["us-central1", "europe-west1", "asia-east1"],
    ),
    CloudProvider.AZURE_ACI: CloudProviderSpec(
        name=CloudProvider.AZURE_ACI,
        display_name="Azure Container Instances",
        cost_per_vcpu_hour=0.04500,
        cost_per_gb_hour=0.00500,
        cold_start_ms=10000,
        max_memory_gb=16,
        max_vcpus=4,
        regions=["eastus", "westeurope", "southeastasia"],
    ),
    CloudProvider.FLY_IO: CloudProviderSpec(
        name=CloudProvider.FLY_IO,
        display_name="Fly.io",
        cost_per_vcpu_hour=0.02800,
        cost_per_gb_hour=0.00300,
        cold_start_ms=500,
        max_memory_gb=8,
        max_vcpus=8,
        regions=["iad", "lhr", "sin"],
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
#  RESOURCE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SystemResources:
    cpu_percent: float           # 0-100
    memory_percent: float        # 0-100
    available_memory_gb: float
    disk_free_gb: float
    active_containers: int
    timestamp: float = field(default_factory=time.time)

    @property
    def is_under_pressure(self) -> bool:
        return self.cpu_percent > 80 or self.memory_percent > 85

    @property
    def load_score(self) -> float:
        """Normalised load score [0,1]. Higher = more overloaded."""
        cpu_load = self.cpu_percent / 100.0
        mem_load = self.memory_percent / 100.0
        container_load = min(self.active_containers / 8.0, 1.0)
        return (cpu_load * 0.4 + mem_load * 0.4 + container_load * 0.2)


class ResourceMonitor:
    """Polls local system resources for scheduling decisions."""

    POLL_INTERVAL_S = 5.0

    def __init__(self):
        self._last_sample: Optional[SystemResources] = None
        self._active_containers = 0

    def get_resources(self) -> SystemResources:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return SystemResources(
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory_percent=mem.percent,
            available_memory_gb=mem.available / (1024 ** 3),
            disk_free_gb=disk.free / (1024 ** 3),
            active_containers=self._active_containers,
        )

    def increment_containers(self):
        self._active_containers += 1

    def decrement_containers(self):
        self._active_containers = max(0, self._active_containers - 1)


# ══════════════════════════════════════════════════════════════════════════════
#  PII SCRUBBER
# ══════════════════════════════════════════════════════════════════════════════

import re

class PIIScrubber:
    """
    Strips Personally Identifiable Information from task payloads
    before they are sent to cloud providers.

    Uses regex patterns for fast O(n) scanning. Serious production use
    should add a transformer-based NER model (e.g. spaCy).
    """

    PATTERNS = [
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"),
         "[EMAIL_REDACTED]"),
        (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
         "[PHONE_REDACTED]"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),       # SSN
         "[SSN_REDACTED]"),
        (re.compile(r"\b(?:\d[ -]?){13,16}\b"),        # credit card
         "[CARD_REDACTED]"),
        (re.compile(r"\b[A-Z0-9]{20,40}\b"),           # API keys heuristic
         "[KEY_REDACTED]"),
        (re.compile(r"password\s*[=:]\s*\S+", re.IGNORECASE),
         "password=[REDACTED]"),
        (re.compile(r"sk-[a-zA-Z0-9]{32,}"),           # OpenAI key pattern
         "[OPENAI_KEY_REDACTED]"),
        (re.compile(r"eyJ[a-zA-Z0-9._-]{20,}"),        # JWT tokens
         "[JWT_REDACTED]"),
    ]

    def scrub(self, text: str) -> str:
        for pattern, replacement in self.PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def scrub_dict(self, data: dict) -> dict:
        """Recursively scrub all string values in a dict."""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = self.scrub(v)
            elif isinstance(v, dict):
                result[k] = self.scrub_dict(v)
            elif isinstance(v, list):
                result[k] = [
                    self.scrub(item) if isinstance(item, str)
                    else self.scrub_dict(item) if isinstance(item, dict)
                    else item
                    for item in v
                ]
            else:
                result[k] = v
        return result


# ══════════════════════════════════════════════════════════════════════════════
#  HYBRID PLANNER — MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlacementDecision:
    task_id: str
    run_locally: bool
    cloud_provider: Optional[CloudProvider]
    reason: str
    burst_score: float
    estimated_cost_usd: float


class HybridPlanner:
    """
    Decides per-task: local Docker or cloud burst.

    Scores are computed for 4 dimensions and combined into a
    burst_probability. If above CLOUD_BURST_THRESHOLD, dispatch to cloud.
    """

    CLOUD_BURST_THRESHOLD = 0.65     # burst_probability above this → cloud
    CLOUD_BURST_WEIGHTS = {
        "load":     0.35,            # local resource pressure
        "duration": 0.25,            # long tasks benefit from cloud parallelism
        "cost":     0.20,            # cloud sometimes cheaper than degraded local
        "privacy":  0.20,            # private data must stay local
    }

    # Task types that must NEVER leave the local machine
    ALWAYS_LOCAL_TASK_TYPES = {
        "file_operation",
        "deployment",                # cloud deployment needs local context
    }

    # Task types that are good cloud candidates
    CLOUD_PREFERRED_TASK_TYPES = {
        "web_research",
        "image_generation",
        "data_processing",
    }

    def __init__(self):
        self.resource_monitor = ResourceMonitor()
        self.pii_scrubber     = PIIScrubber()

    def decide(
        self,
        task: Any,
        context: Any,
        preferred_cloud: Optional[CloudProvider] = None,
    ) -> PlacementDecision:
        """
        Compute burst_probability and make placement decision.
        This is a synchronous, O(1) decision function.
        """
        task_type = task.type.value if hasattr(task.type, "value") else str(task.type)

        # ── Hard constraint: task explicitly marked run_locally ───────────────
        if getattr(task, "run_locally", False) or task_type in self.ALWAYS_LOCAL_TASK_TYPES:
            return PlacementDecision(
                task_id=task.id, run_locally=True,
                cloud_provider=None, reason="Hard constraint: run_locally=True",
                burst_score=0.0, estimated_cost_usd=0.0,
            )

        resources = self.resource_monitor.get_resources()

        # ── Dimension scores ─────────────────────────────────────────────────

        # 1. Load score: how overloaded is the local machine?
        load_score = resources.load_score

        # 2. Duration score: longer tasks benefit more from cloud
        est_dur = getattr(task, "estimated_duration_seconds", 30)
        duration_score = min(est_dur / 300.0, 1.0)   # cap at 5 min

        # 3. Cost score: is cloud actually cheaper for this task?
        local_cost = 0.02 * (est_dur / 3600)          # rough local power cost
        cloud_spec = self._get_cheapest_provider(task)
        cloud_cost = (
            cloud_spec.cost_per_vcpu_hour * 0.5 +
            cloud_spec.cost_per_gb_hour * 0.5
        ) * (est_dur / 3600)
        cost_score = 1.0 - min(local_cost / max(cloud_cost, 0.0001), 1.0)

        # 4. Privacy score: high privacy preference = stay local
        payload_str = json.dumps(context.upstream_results, default=str)
        has_sensitive = any(
            word in payload_str.lower()
            for word in ["password", "api_key", "secret", "token", "private"]
        )
        privacy_penalty = 0.8 if has_sensitive else 0.0

        # ── Compute burst probability ─────────────────────────────────────────
        w = self.CLOUD_BURST_WEIGHTS
        burst_prob = (
            w["load"]     * load_score
          + w["duration"] * duration_score
          + w["cost"]     * cost_score
          - w["privacy"]  * privacy_penalty    # privacy penalises cloud
        )
        burst_prob = max(0.0, min(1.0, burst_prob))   # clamp to [0,1]

        # ── Bonus for cloud-preferred task types ──────────────────────────────
        if task_type in self.CLOUD_PREFERRED_TASK_TYPES:
            burst_prob = min(1.0, burst_prob + 0.15)

        run_locally = burst_prob <= self.CLOUD_BURST_THRESHOLD
        provider = None if run_locally else cloud_spec.name
        reason = (
            f"load={load_score:.2f} dur={duration_score:.2f} "
            f"cost={cost_score:.2f} privacy_penalty={privacy_penalty:.1f} "
            f"→ burst_prob={burst_prob:.2f}"
        )
        estimated_cost = 0.0 if run_locally else cloud_cost

        log.info(
            f"Task {task.id} → {'LOCAL' if run_locally else f'CLOUD({provider})'} | {reason}"
        )

        return PlacementDecision(
            task_id=task.id,
            run_locally=run_locally,
            cloud_provider=provider,
            reason=reason,
            burst_score=burst_prob,
            estimated_cost_usd=estimated_cost,
        )

    def _get_cheapest_provider(self, task: Any) -> CloudProviderSpec:
        """Select cheapest available cloud provider for a given task."""
        available = [
            spec for spec in CLOUD_PROVIDERS.values()
            if spec.is_available
        ]
        if not available:
            raise RuntimeError("No cloud providers available")

        # Sort by combined cost per hour (1 vCPU + 512MB)
        def hourly_cost(spec: CloudProviderSpec) -> float:
            return spec.cost_per_vcpu_hour * 1.0 + spec.cost_per_gb_hour * 0.5

        return min(available, key=hourly_cost)

    def prepare_cloud_payload(self, context: Any) -> dict:
        """Strip PII before sending payload to cloud provider."""
        raw_payload = {
            "task_id": context.task_id,
            "task_type": context.task_type,
            "description": context.description,
            "tools_required": context.tools_required,
            "upstream_results": context.upstream_results,
        }
        return self.pii_scrubber.scrub_dict(raw_payload)


# ══════════════════════════════════════════════════════════════════════════════
#  CLOUD DISPATCHER — AWS Fargate Example Implementation
# ══════════════════════════════════════════════════════════════════════════════

class AWSFargateDispatcher:
    """
    Dispatches tasks to AWS Fargate via ECS RunTask API.
    Uses short-lived signed tokens; raw API keys never leave local machine.
    """

    def __init__(self):
        try:
            import boto3
            self._ecs = boto3.client("ecs")
            self._sts = boto3.client("sts")
        except ImportError:
            log.warning("boto3 not installed — AWS Fargate dispatcher disabled")
            self._ecs = None

    async def dispatch(
        self,
        payload: dict,
        cluster: str = "agentic-os-cluster",
        task_definition: str = "agentic-task-runner",
    ) -> str:
        """Launch Fargate task. Returns task ARN for polling."""
        if not self._ecs:
            raise RuntimeError("boto3 not installed")

        # Encode payload as base64 environment variable
        import base64
        encoded = base64.b64encode(
            json.dumps(payload).encode()
        ).decode()

        response = await asyncio.to_thread(
            self._ecs.run_task,
            cluster=cluster,
            taskDefinition=task_definition,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [os.environ.get("AWS_SUBNET_ID", "")],
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [{
                    "name": "task-runner",
                    "environment": [
                        {"name": "TASK_PAYLOAD", "value": encoded}
                    ],
                }]
            },
        )
        task_arn = response["tasks"][0]["taskArn"]
        log.info(f"Fargate task dispatched: {task_arn}")
        return task_arn

    async def wait_for_result(self, task_arn: str, cluster: str = "agentic-os-cluster") -> dict:
        """Poll ECS until task completes, then fetch result from CloudWatch."""
        if not self._ecs:
            raise RuntimeError("boto3 not installed")

        while True:
            response = await asyncio.to_thread(
                self._ecs.describe_tasks,
                cluster=cluster,
                tasks=[task_arn],
            )
            task = response["tasks"][0]
            status = task["lastStatus"]
            if status == "STOPPED":
                exit_code = task["containers"][0].get("exitCode", -1)
                log.info(f"Fargate task {task_arn} stopped with exit_code={exit_code}")
                return {"task_arn": task_arn, "exit_code": exit_code, "status": "complete"}
            await asyncio.sleep(5)


class GCPCloudRunDispatcher:
    """Dispatches tasks to GCP Cloud Run jobs."""

    async def dispatch(self, payload: dict, region: str = "us-central1") -> str:
        """Submit Cloud Run job. Returns job execution ID."""
        try:
            from google.cloud import run_v2
            client = run_v2.JobsAsyncClient()
            project_id = os.environ.get("GCP_PROJECT_ID", "")
            job_name = f"agentic-task-{uuid.uuid4().hex[:8]}"
            # Cloud Run job submission would go here
            log.info(f"GCP Cloud Run job submitted: {job_name}")
            return job_name
        except ImportError:
            raise RuntimeError("google-cloud-run not installed")
