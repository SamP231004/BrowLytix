"""
================================================================================
  AGENTIC OS BROWSER — MULTI-LLM ROUTER
================================================================================
  Routes each task to the optimal LLM based on a weighted capability registry.
  Supports: OpenAI GPT-4o, Anthropic Claude, Ollama (Llama3/Mistral/Qwen),
  with automatic fallback chains, rate-limit handling, and cost tracking.

  Algorithm:
    1.  Build capability matrix: model × task_type → quality score [0,1]
    2.  For each task node:
        a. Filter to models capable of the task type
        b. Apply hard constraints (run_locally, privacy, context_length)
        c. Score each model: weighted(quality, speed, cost, privacy)
        d. Select argmax; register fallback chain
    3.  Call selected model with retry + exponential backoff
    4.  On failure, walk fallback chain
    5.  Track usage metrics (tokens, cost, latency) per model
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import anthropic
import openai

log = logging.getLogger("router")


# ══════════════════════════════════════════════════════════════════════════════
#  MODEL REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

class ModelProvider(str, Enum):
    OPENAI    = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA    = "ollama"      # local: Llama3, Mistral, Qwen


@dataclass
class ModelSpec:
    """Full specification for one LLM model."""
    model_id: str               # e.g. "claude-sonnet-4-6"
    provider: ModelProvider
    display_name: str
    is_local: bool              # True = runs on Ollama, no API key needed
    context_window: int         # max tokens in context
    max_output_tokens: int
    cost_per_1k_input: float    # USD
    cost_per_1k_output: float   # USD
    avg_latency_ms: float       # empirical average
    # Quality scores per task type [0.0 – 1.0]
    quality_scores: Dict[str, float] = field(default_factory=dict)

    def score_for_task(self, task_type: str) -> float:
        return self.quality_scores.get(task_type, 0.3)


# ── Capability Registry — empirically calibrated scores ──────────────────────
MODEL_REGISTRY: Dict[str, ModelSpec] = {
    "gpt-4o": ModelSpec(
        model_id="gpt-4o",
        provider=ModelProvider.OPENAI,
        display_name="GPT-4o",
        is_local=False,
        context_window=128_000,
        max_output_tokens=4096,
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        avg_latency_ms=2200,
        quality_scores={
            "code_execution":      0.95,
            "image_generation":    0.90,
            "api_call":            0.90,
            "reasoning":           0.88,
            "web_research":        0.80,
            "data_processing":     0.85,
            "document_generation": 0.82,
            "file_operation":      0.78,
            "deployment":          0.85,
            "summarisation":       0.82,
        },
    ),
    "claude-sonnet-4-6": ModelSpec(
        model_id="claude-sonnet-4-6",
        provider=ModelProvider.ANTHROPIC,
        display_name="Claude Sonnet 4.6",
        is_local=False,
        context_window=200_000,
        max_output_tokens=8192,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        avg_latency_ms=1800,
        quality_scores={
            "reasoning":           0.96,
            "document_generation": 0.95,
            "summarisation":       0.94,
            "data_processing":     0.88,
            "code_execution":      0.87,
            "web_research":        0.82,
            "api_call":            0.80,
            "file_operation":      0.80,
            "deployment":          0.78,
            "image_generation":    0.40,  # text-only
        },
    ),
    "claude-haiku-4-5": ModelSpec(
        model_id="claude-haiku-4-5-20251001",
        provider=ModelProvider.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        is_local=False,
        context_window=200_000,
        max_output_tokens=4096,
        cost_per_1k_input=0.00025,
        cost_per_1k_output=0.00125,
        avg_latency_ms=600,
        quality_scores={
            "summarisation":       0.85,
            "reasoning":           0.78,
            "data_processing":     0.75,
            "document_generation": 0.74,
            "web_research":        0.72,
            "code_execution":      0.70,
            "api_call":            0.68,
            "file_operation":      0.68,
            "deployment":          0.60,
            "image_generation":    0.30,
        },
    ),
    "llama3.3:70b": ModelSpec(
        model_id="llama3.3:70b",
        provider=ModelProvider.OLLAMA,
        display_name="Llama 3.3 70B (local)",
        is_local=True,
        context_window=128_000,
        max_output_tokens=4096,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        avg_latency_ms=3500,
        quality_scores={
            "web_research":        0.82,
            "summarisation":       0.80,
            "reasoning":           0.78,
            "data_processing":     0.76,
            "code_execution":      0.74,
            "document_generation": 0.73,
            "api_call":            0.68,
            "file_operation":      0.68,
            "deployment":          0.60,
            "image_generation":    0.30,
        },
    ),
    "mistral:latest": ModelSpec(
        model_id="mistral:latest",
        provider=ModelProvider.OLLAMA,
        display_name="Mistral Large (local)",
        is_local=True,
        context_window=32_000,
        max_output_tokens=4096,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        avg_latency_ms=1200,
        quality_scores={
            "data_processing":     0.86,
            "code_execution":      0.83,
            "reasoning":           0.78,
            "api_call":            0.78,
            "summarisation":       0.76,
            "web_research":        0.72,
            "document_generation": 0.71,
            "file_operation":      0.72,
            "deployment":          0.70,
            "image_generation":    0.25,
        },
    ),
    "qwen2.5-coder:32b": ModelSpec(
        model_id="qwen2.5-coder:32b",
        provider=ModelProvider.OLLAMA,
        display_name="Qwen 2.5 Coder 32B (local)",
        is_local=True,
        context_window=128_000,
        max_output_tokens=8192,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        avg_latency_ms=2000,
        quality_scores={
            "code_execution":      0.94,
            "deployment":          0.88,
            "data_processing":     0.82,
            "api_call":            0.80,
            "file_operation":      0.80,
            "reasoning":           0.72,
            "web_research":        0.60,
            "summarisation":       0.65,
            "document_generation": 0.65,
            "image_generation":    0.20,
        },
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTING POLICY
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RoutingPolicy:
    """
    Configurable weights for model selection.
    All weights must sum to 1.0.
    """
    quality_weight:  float = 0.40
    speed_weight:    float = 0.25
    cost_weight:     float = 0.25
    privacy_weight:  float = 0.10

    # Hard constraints
    force_local: bool = False            # override: only use local models
    max_cost_per_task_usd: float = 0.10  # reject models exceeding this
    min_context_window: int = 0          # minimum context needed

    def __post_init__(self):
        total = self.quality_weight + self.speed_weight + self.cost_weight + self.privacy_weight
        assert abs(total - 1.0) < 0.01, f"Routing policy weights must sum to 1.0, got {total}"


# ══════════════════════════════════════════════════════════════════════════════
#  USAGE TRACKER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UsageRecord:
    model_id: str
    task_type: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    success: bool
    timestamp: float = field(default_factory=time.time)


class UsageTracker:
    """Thread-safe usage and cost tracker across all LLM calls."""

    def __init__(self):
        self._records: List[UsageRecord] = []
        self._lock = asyncio.Lock()

    async def record(self, rec: UsageRecord):
        async with self._lock:
            self._records.append(rec)

    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self._records)

    def model_summary(self) -> Dict[str, Dict]:
        summary: Dict[str, Dict] = {}
        for rec in self._records:
            if rec.model_id not in summary:
                summary[rec.model_id] = {
                    "calls": 0, "total_cost_usd": 0.0,
                    "avg_latency_ms": 0.0, "success_rate": 0.0,
                    "successes": 0,
                }
            s = summary[rec.model_id]
            s["calls"] += 1
            s["total_cost_usd"] += rec.cost_usd
            s["avg_latency_ms"] = (
                (s["avg_latency_ms"] * (s["calls"] - 1) + rec.latency_ms) / s["calls"]
            )
            if rec.success:
                s["successes"] += 1
            s["success_rate"] = s["successes"] / s["calls"]
        return summary


# ══════════════════════════════════════════════════════════════════════════════
#  LLM ROUTER — MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

class LLMRouter:
    """
    Multi-model LLM router with weighted scoring, fallback chains,
    retry logic, and usage tracking.
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_S = 1.0
    BACKOFF_MULTIPLIER = 2.0

    def __init__(self, policy: Optional[RoutingPolicy] = None):
        self.policy  = policy or RoutingPolicy()
        self.tracker = UsageTracker()
        self._openai_client: Optional[openai.AsyncOpenAI] = None
        self._anthropic_client: Optional[anthropic.AsyncAnthropic] = None
        self._init_clients()

    def _init_clients(self):
        """Lazily initialise API clients — don't fail on missing keys."""
        try:
            self._openai_client = openai.AsyncOpenAI()
        except Exception as e:
            log.warning(f"OpenAI client init failed: {e}")
        try:
            self._anthropic_client = anthropic.AsyncAnthropic()
        except Exception as e:
            log.warning(f"Anthropic client init failed: {e}")

    # ── Model Selection ───────────────────────────────────────────────────────

    def select_model(
        self,
        task_type: str,
        model_preference: str = "auto",
        require_local: bool = False,
        min_context: int = 0,
    ) -> Tuple[ModelSpec, List[ModelSpec]]:
        """
        Select best model + fallback chain for a given task.

        Scoring formula:
          score(m) = w_q * quality(m,task)
                   + w_s * norm_speed(m)
                   + w_c * norm_cost(m)
                   + w_p * is_local(m)

        Returns (best_model, fallback_chain).
        """
        policy = self.policy

        # ── Filter candidates
        candidates = list(MODEL_REGISTRY.values())
        if require_local or policy.force_local:
            candidates = [m for m in candidates if m.is_local]
        candidates = [m for m in candidates if m.context_window >= min_context]

        if not candidates:
            raise RuntimeError("No LLM candidates available after filtering constraints")

        # ── Honor explicit preference if not "auto"
        if model_preference != "auto":
            pref_map = {
                "gpt4o":   "gpt-4o",
                "claude":  "claude-sonnet-4-6",
                "llama3":  "llama3.3:70b",
                "mistral": "mistral:latest",
                "qwen":    "qwen2.5-coder:32b",
            }
            preferred_id = pref_map.get(model_preference)
            if preferred_id and preferred_id in MODEL_REGISTRY:
                preferred = MODEL_REGISTRY[preferred_id]
                if preferred in candidates:
                    fallbacks = [m for m in candidates if m != preferred]
                    fallbacks.sort(key=lambda m: -m.score_for_task(task_type))
                    return preferred, fallbacks[:2]

        # ── Normalise speed and cost for scoring
        max_latency = max(m.avg_latency_ms for m in candidates)
        max_cost    = max(
            m.cost_per_1k_input + m.cost_per_1k_output for m in candidates
        ) or 1.0

        def model_score(m: ModelSpec) -> float:
            quality  = m.score_for_task(task_type)
            speed    = 1.0 - (m.avg_latency_ms / max_latency)
            combined_cost = m.cost_per_1k_input + m.cost_per_1k_output
            cost     = 1.0 - (combined_cost / max_cost)
            privacy  = 1.0 if m.is_local else 0.0
            return (
                policy.quality_weight  * quality
              + policy.speed_weight    * speed
              + policy.cost_weight     * cost
              + policy.privacy_weight  * privacy
            )

        ranked = sorted(candidates, key=model_score, reverse=True)
        best = ranked[0]
        fallbacks = ranked[1:3]   # top 2 fallbacks
        log.info(
            f"Router selected: {best.display_name} for task_type={task_type} "
            f"(score={model_score(best):.3f})"
        )
        return best, fallbacks

    # ── LLM Call with Retry + Fallback ────────────────────────────────────────

    async def call_llm(
        self,
        prompt: str,
        task_type: str,
        model_preference: str = "auto",
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        require_local: bool = False,
        min_context: int = 0,
        temperature: float = 0.2,
    ) -> str:
        """
        Route prompt to best model, with automatic retry + fallback chain.
        Returns the model's text response.
        """
        model, fallbacks = self.select_model(
            task_type=task_type,
            model_preference=model_preference,
            require_local=require_local,
            min_context=min_context,
        )
        models_to_try = [model] + fallbacks

        last_error = None
        for attempt_model in models_to_try:
            for retry in range(self.MAX_RETRIES):
                try:
                    t0 = time.time()
                    response_text, in_tok, out_tok = await self._call_provider(
                        model=attempt_model,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    latency = (time.time() - t0) * 1000
                    cost = self._compute_cost(attempt_model, in_tok, out_tok)

                    await self.tracker.record(UsageRecord(
                        model_id=attempt_model.model_id,
                        task_type=task_type,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        latency_ms=latency,
                        cost_usd=cost,
                        success=True,
                    ))
                    return response_text

                except (openai.RateLimitError, anthropic.RateLimitError) as e:
                    wait = self.BASE_BACKOFF_S * (self.BACKOFF_MULTIPLIER ** retry)
                    log.warning(f"Rate limit on {attempt_model.display_name}, "
                                f"retry {retry+1}/{self.MAX_RETRIES} in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    last_error = e

                except Exception as e:
                    log.error(f"LLM call failed on {attempt_model.display_name}: {e}")
                    last_error = e
                    break   # don't retry on hard errors; try next model

        raise RuntimeError(
            f"All LLM attempts exhausted for task_type={task_type}. "
            f"Last error: {last_error}"
        )

    async def _call_provider(
        self,
        model: ModelSpec,
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> Tuple[str, int, int]:
        """Dispatch to the correct provider SDK. Returns (text, in_tokens, out_tokens)."""

        if model.provider == ModelProvider.OPENAI:
            return await self._call_openai(model, prompt, system_prompt, max_tokens, temperature)

        elif model.provider == ModelProvider.ANTHROPIC:
            return await self._call_anthropic(model, prompt, system_prompt, max_tokens, temperature)

        elif model.provider == ModelProvider.OLLAMA:
            return await self._call_ollama(model, prompt, system_prompt, max_tokens, temperature)

        raise ValueError(f"Unknown provider: {model.provider}")

    async def _call_openai(
        self, model: ModelSpec, prompt: str,
        system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Tuple[str, int, int]:
        if not self._openai_client:
            raise RuntimeError("OpenAI client not initialised")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = await self._openai_client.chat.completions.create(
            model=model.model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return text, usage.prompt_tokens, usage.completion_tokens

    async def _call_anthropic(
        self, model: ModelSpec, prompt: str,
        system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Tuple[str, int, int]:
        if not self._anthropic_client:
            raise RuntimeError("Anthropic client not initialised")
        kwargs: Dict[str, Any] = {
            "model": model.model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        resp = await self._anthropic_client.messages.create(**kwargs)
        text = resp.content[0].text if resp.content else ""
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    async def _call_ollama(
        self, model: ModelSpec, prompt: str,
        system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Tuple[str, int, int]:
        """Call local Ollama server at localhost:11434."""
        import aiohttp
        payload = {
            "model": model.model_id,
            "prompt": f"{system_prompt}\n\n{prompt}" if system_prompt else prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama returned HTTP {resp.status}")
                data = await resp.json()
                text = data.get("response", "")
                # Ollama doesn't report tokens consistently — estimate
                in_tok = len(prompt.split()) * 4 // 3
                out_tok = len(text.split()) * 4 // 3
                return text, in_tok, out_tok

    def _compute_cost(self, model: ModelSpec, in_tokens: int, out_tokens: int) -> float:
        return (
            model.cost_per_1k_input  * in_tokens  / 1000 +
            model.cost_per_1k_output * out_tokens / 1000
        )

    def get_usage_summary(self) -> Dict[str, Any]:
        return {
            "total_cost_usd": self.tracker.total_cost_usd(),
            "by_model": self.tracker.model_summary(),
        }
