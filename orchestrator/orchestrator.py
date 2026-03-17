"""
================================================================================
  AGENTIC OS BROWSER — ORCHESTRATOR AGENT
================================================================================
  The central nervous system of the Agentic OS Browser.
  Receives raw user intent via WebSocket, parses it into a structured JSON DAG,
  manages task lifecycle, aggregates results, and streams progress back to the
  browser UI.

  Algorithm:
    1.  Receive raw natural-language goal from browser Intent Bar
    2.  Run IntentParser → structured IntentSpec (domain, entities, constraints)
    3.  Run DAGBuilder → validated TaskGraph (nodes + directed edges)
    4.  Validate DAG for cycles (Kahn's algorithm)
    5.  Push TaskGraph to WorkflowEngine for execution
    6.  Stream TaskEvent updates back to browser via WebSocket
    7.  Aggregate final results and render in browser
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from dotenv import load_dotenv
import os

load_dotenv()

CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY")

import websockets
from websockets.server import WebSocketServerProtocol

# ── Internal imports ──────────────────────────────────────────────────────────
from router.router import LLMRouter, RoutingPolicy
from workflow_engine.engine import WorkflowEngine
from runtime.container_manager import ContainerManager

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | ORCHESTRATOR | %(message)s"
)
log = logging.getLogger("orchestrator")


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

class TaskType(str, Enum):
    WEB_RESEARCH       = "web_research"
    CODE_EXECUTION     = "code_execution"
    DATA_PROCESSING    = "data_processing"
    FILE_OPERATION     = "file_operation"
    API_CALL           = "api_call"
    IMAGE_GENERATION   = "image_generation"
    DOCUMENT_GENERATION= "document_generation"
    DEPLOYMENT         = "deployment"
    REASONING          = "reasoning"
    SUMMARISATION      = "summarisation"


class TaskState(str, Enum):
    PENDING   = "pending"
    READY     = "ready"       # all dependencies met
    RUNNING   = "running"
    COMPLETE  = "complete"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    RETRYING  = "retrying"


class PreferredLLM(str, Enum):
    GPT4O    = "gpt4o"
    CLAUDE   = "claude"
    LLAMA3   = "llama3"
    MISTRAL  = "mistral"
    QWEN     = "qwen"
    AUTO     = "auto"         # router decides


@dataclass
class TaskNode:
    """Represents a single atomic task in the workflow DAG."""
    id: str
    type: TaskType
    description: str
    inputs: List[str]                    # task IDs whose output this consumes
    tools_required: List[str]
    estimated_duration_seconds: int
    preferred_llm: PreferredLLM
    run_locally: bool
    state: TaskState = TaskState.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    container_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def duration_ms(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at) * 1000
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["state"] = self.state.value
        d["preferred_llm"] = self.preferred_llm.value
        return d


@dataclass
class TaskGraph:
    """A validated directed acyclic graph of TaskNodes."""
    goal: str
    tasks: Dict[str, TaskNode]           # id → TaskNode
    edges: Dict[str, Set[str]]           # id → set of downstream task IDs
    reverse_edges: Dict[str, Set[str]]   # id → set of upstream task IDs
    created_at: float = field(default_factory=time.time)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def ready_tasks(self) -> List[TaskNode]:
        """Return tasks whose all dependencies are COMPLETE."""
        ready = []
        for task in self.tasks.values():
            if task.state != TaskState.PENDING:
                continue
            deps_done = all(
                self.tasks[dep_id].state == TaskState.COMPLETE
                for dep_id in task.inputs
            )
            if deps_done:
                ready.append(task)
        return ready

    def is_complete(self) -> bool:
        return all(t.state == TaskState.COMPLETE for t in self.tasks.values())

    def has_failed(self) -> bool:
        return any(
            t.state == TaskState.FAILED and t.retry_count >= t.max_retries
            for t in self.tasks.values()
        )


@dataclass
class IntentSpec:
    """Parsed, structured representation of user intent."""
    raw_goal: str
    domain: str                          # e.g. "market_research", "software_dev"
    entities: List[str]                  # key nouns extracted from goal
    constraints: Dict[str, Any]          # e.g. {"max_duration": 300, "privacy": True}
    suggested_task_types: List[TaskType]
    complexity: str                      # "low" | "medium" | "high"


@dataclass
class TaskEvent:
    """Streamed to browser to update UI."""
    session_id: str
    task_id: Optional[str]
    event_type: str                      # "task_started" | "task_complete" | "task_failed" | "dag_complete"
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "session_id": self.session_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        })


# ══════════════════════════════════════════════════════════════════════════════
#  INTENT PARSER
# ══════════════════════════════════════════════════════════════════════════════

class IntentParser:
    """
    Converts raw natural language goal into a structured IntentSpec.

    Algorithm:
      1. Classify domain using keyword heuristics + LLM zero-shot classification
      2. Extract entities (nouns, named entities) via LLM
      3. Detect constraints (privacy, speed, budget) from modifiers
      4. Estimate complexity from entity count + action verbs
    """

    DOMAIN_KEYWORDS = {
        "market_research":  ["market", "competitor", "industry", "share", "analysis", "company"],
        "software_dev":     ["code", "build", "deploy", "api", "app", "function", "debug", "refactor"],
        "data_analysis":    ["data", "analyse", "chart", "graph", "csv", "statistics", "plot"],
        "content_creation": ["write", "report", "document", "article", "blog", "summary"],
        "web_automation":   ["scrape", "crawl", "download", "extract", "website", "form"],
        "finance":          ["price", "stock", "financial", "revenue", "cost", "budget"],
        "research":         ["research", "find", "search", "compare", "evaluate", "study"],
    }

    def __init__(self, llm_router: LLMRouter):
        self.router = llm_router

    def classify_domain(self, goal: str) -> str:
        """Heuristic keyword-based domain classification (O(n) scan)."""
        goal_lower = goal.lower()
        scores: Dict[str, int] = defaultdict(int)
        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            for kw in keywords:
                if kw in goal_lower:
                    scores[domain] += 1
        if not scores:
            return "general"
        return max(scores, key=scores.__getitem__)

    def estimate_complexity(self, goal: str) -> str:
        """Estimate task complexity from word count and action verb density."""
        words = goal.split()
        action_verbs = {"research", "build", "analyse", "deploy", "compare",
                        "generate", "write", "scrape", "integrate", "visualise"}
        verb_count = sum(1 for w in words if w.lower().strip(",.") in action_verbs)
        if len(words) < 10 and verb_count <= 1:
            return "low"
        if len(words) < 25 and verb_count <= 3:
            return "medium"
        return "high"

    async def parse(self, raw_goal: str) -> IntentSpec:
        """Full async intent parsing pipeline."""
        log.info(f"Parsing intent: '{raw_goal[:80]}...'")

        domain = self.classify_domain(raw_goal)
        complexity = self.estimate_complexity(raw_goal)

        # LLM call to extract entities and suggest task types
        prompt = f"""Analyse this user goal and return ONLY valid JSON:
Goal: "{raw_goal}"

Return:
{{
  "entities": ["list", "of", "key", "nouns"],
  "constraints": {{"privacy": bool, "speed_priority": bool}},
  "suggested_task_types": ["web_research|code_execution|data_processing|file_operation|api_call|image_generation|document_generation|deployment|reasoning|summarisation"]
}}"""

        try:
            response = await self.router.call_llm(
                model_preference="claude",
                prompt=prompt,
                task_type=TaskType.REASONING,
                max_tokens=500,
            )
            parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
            entities = parsed.get("entities", [])
            constraints = parsed.get("constraints", {})
            suggested = [TaskType(t) for t in parsed.get("suggested_task_types", [])
                        if t in TaskType._value2member_map_]
        except Exception as e:
            log.warning(f"LLM entity extraction failed ({e}), using heuristics")
            entities = [w for w in raw_goal.split() if len(w) > 4][:5]
            constraints = {}
            suggested = [TaskType.REASONING]

        spec = IntentSpec(
            raw_goal=raw_goal,
            domain=domain,
            entities=entities,
            constraints=constraints,
            suggested_task_types=suggested,
            complexity=complexity,
        )
        log.info(f"IntentSpec: domain={domain}, complexity={complexity}, entities={entities}")
        return spec


# ══════════════════════════════════════════════════════════════════════════════
#  DAG BUILDER
# ══════════════════════════════════════════════════════════════════════════════

DAG_SYSTEM_PROMPT = """\
You are a workflow planning agent for an agentic AI browser system.
Given a user goal and intent analysis, decompose it into the MINIMUM set of
atomic tasks as a valid JSON DAG.

STRICT RULES:
- Return ONLY valid JSON. Zero preamble. Zero explanation.
- Every task id must be unique (t1, t2, t3 ...).
- inputs[] references ids of tasks that must complete before this task starts.
- No circular dependencies.
- Maximise parallelism (independent tasks have empty inputs[]).
- tools_required uses lowercase package names (python3, pandas, playwright, etc).
- run_locally=true for tasks handling private/sensitive data.
- preferred_llm: one of [gpt4o, claude, llama3, mistral, qwen, auto]

OUTPUT SCHEMA:
{
  "goal": "string",
  "tasks": [
    {
      "id": "t1",
      "type": "web_research|code_execution|data_processing|file_operation|api_call|image_generation|document_generation|deployment|reasoning|summarisation",
      "description": "string",
      "inputs": ["t_id", ...],
      "tools_required": ["tool", ...],
      "estimated_duration_seconds": int,
      "preferred_llm": "gpt4o|claude|llama3|mistral|qwen|auto",
      "run_locally": bool
    }
  ]
}"""


class DAGBuilder:
    """
    Converts IntentSpec into a validated TaskGraph.

    Algorithm:
      1. Build LLM prompt with intent context
      2. Call planning LLM (Claude Sonnet) → raw JSON
      3. Parse and validate JSON schema
      4. Build adjacency lists (edges + reverse_edges)
      5. Run Kahn's topological sort to detect cycles
      6. Return validated TaskGraph
    """

    def __init__(self, llm_router: LLMRouter):
        self.router = llm_router

    async def build(self, intent: IntentSpec) -> TaskGraph:
        log.info(f"Building DAG for goal: '{intent.raw_goal[:60]}...'")

        user_prompt = f"""
Goal: "{intent.raw_goal}"
Domain: {intent.domain}
Complexity: {intent.complexity}
Key entities: {intent.entities}
Constraints: {intent.constraints}

Decompose this into an optimal parallel task DAG following the schema above."""

        raw_json = await self.router.call_llm(
            model_preference="claude",
            prompt=user_prompt,
            system_prompt=DAG_SYSTEM_PROMPT,
            task_type=TaskType.REASONING,
            max_tokens=2000,
        )

        dag_data = self._parse_dag_json(raw_json)
        graph = self._build_graph(intent.raw_goal, dag_data)
        self._validate_dag(graph)
        log.info(f"DAG built: {len(graph.tasks)} tasks, session={graph.session_id}")
        return graph

    def _parse_dag_json(self, raw: str) -> dict:
        """Robust JSON extraction — handles LLM markdown fences."""
        cleaned = raw.strip()
        if "```" in cleaned:
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            cleaned = cleaned[start:end]
        data = json.loads(cleaned)
        if "tasks" not in data:
            raise ValueError("DAG JSON missing 'tasks' key")
        return data

    def _build_graph(self, goal: str, data: dict) -> TaskGraph:
        """Build TaskGraph with forward and reverse adjacency lists."""
        tasks: Dict[str, TaskNode] = {}
        edges: Dict[str, Set[str]] = defaultdict(set)
        reverse_edges: Dict[str, Set[str]] = defaultdict(set)

        for raw_task in data["tasks"]:
            node = TaskNode(
                id=raw_task["id"],
                type=TaskType(raw_task["type"]),
                description=raw_task["description"],
                inputs=raw_task.get("inputs", []),
                tools_required=raw_task.get("tools_required", []),
                estimated_duration_seconds=raw_task.get("estimated_duration_seconds", 30),
                preferred_llm=PreferredLLM(raw_task.get("preferred_llm", "auto")),
                run_locally=raw_task.get("run_locally", False),
            )
            tasks[node.id] = node

        for task in tasks.values():
            for dep_id in task.inputs:
                if dep_id not in tasks:
                    raise ValueError(f"Task {task.id} depends on unknown task {dep_id}")
                edges[dep_id].add(task.id)
                reverse_edges[task.id].add(dep_id)

        return TaskGraph(
            goal=goal,
            tasks=tasks,
            edges=dict(edges),
            reverse_edges=dict(reverse_edges),
        )

    def _validate_dag(self, graph: TaskGraph) -> None:
        """
        Kahn's Algorithm for cycle detection — O(V + E).
        Raises ValueError if a cycle is detected.
        """
        in_degree = {tid: len(task.inputs) for tid, task in graph.tasks.items()}
        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        visited_count = 0

        while queue:
            tid = queue.popleft()
            visited_count += 1
            for downstream_id in graph.edges.get(tid, set()):
                in_degree[downstream_id] -= 1
                if in_degree[downstream_id] == 0:
                    queue.append(downstream_id)

        if visited_count != len(graph.tasks):
            raise ValueError(
                f"DAG cycle detected! Visited {visited_count}/{len(graph.tasks)} nodes. "
                "The LLM produced a circular dependency."
            )
        log.info("DAG cycle validation passed (Kahn's algorithm)")


# ══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR — MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    Master orchestrator — coordinates all subsystems.

    Runs as an asyncio daemon with a WebSocket server that the Chromium
    browser connects to on localhost:7771.
    """

    WS_HOST = "localhost"
    WS_PORT = 7771

    def __init__(self):
        self.router         = LLMRouter()
        self.intent_parser  = IntentParser(self.router)
        self.dag_builder    = DAGBuilder(self.router)
        self.workflow_engine= WorkflowEngine(self.router)
        self.container_mgr  = ContainerManager()

        # Active sessions: session_id → TaskGraph
        self.sessions: Dict[str, TaskGraph] = {}
        # Connected WebSocket clients: session_id → websocket
        self.clients: Dict[str, WebSocketServerProtocol] = {}
        # Event listeners registered by session
        self._event_listeners: Dict[str, List[Callable]] = defaultdict(list)

    # ── WebSocket Server ──────────────────────────────────────────────────────

    async def start(self):
        """Start the WebSocket server and listen for browser connections."""
        log.info(f"Orchestrator starting on ws://{self.WS_HOST}:{self.WS_PORT}")
        async with websockets.serve(
            self._handle_connection,
            self.WS_HOST,
            self.WS_PORT,
            ping_interval=20,
            ping_timeout=60,
            max_size=10 * 1024 * 1024,   # 10MB max message
        ):
            log.info("Orchestrator ready — awaiting browser connections")
            await asyncio.Future()        # run forever

    async def _handle_connection(self, ws: WebSocketServerProtocol):
        """Handle a new browser WebSocket connection."""
        client_id = str(uuid.uuid4())[:8]
        log.info(f"Browser connected: client={client_id}")
        try:
            async for raw_message in ws:
                await self._dispatch_message(ws, client_id, raw_message)
        except websockets.exceptions.ConnectionClosedOK:
            log.info(f"Browser disconnected: client={client_id}")
        except Exception as e:
            log.error(f"WebSocket error for client={client_id}: {e}")
        finally:
            # Clean up sessions associated with this client
            for sid, graph in list(self.sessions.items()):
                if self.clients.get(sid) == ws:
                    del self.clients[sid]

    async def _dispatch_message(
        self, ws: WebSocketServerProtocol, client_id: str, raw: str
    ):
        """Route incoming browser messages to handlers."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps({"error": "Invalid JSON"}))
            return

        action = msg.get("action")
        handlers = {
            "execute_goal": self._handle_execute_goal,
            "cancel_workflow": self._handle_cancel_workflow,
            "get_status":   self._handle_get_status,
            "retry_task":   self._handle_retry_task,
        }
        handler = handlers.get(action)
        if handler:
            await handler(ws, client_id, msg)
        else:
            await ws.send(json.dumps({"error": f"Unknown action: {action}"}))

    # ── Goal Execution Pipeline ───────────────────────────────────────────────

    async def _handle_execute_goal(
        self, ws: WebSocketServerProtocol, client_id: str, msg: dict
    ):
        """
        Main execution pipeline:
          IntentParse → DAGBuild → WorkflowExecute → Stream results
        """
        raw_goal = msg.get("goal", "").strip()
        if not raw_goal:
            await ws.send(json.dumps({"error": "Empty goal"}))
            return

        session_id = str(uuid.uuid4())
        self.clients[session_id] = ws

        await self._emit(session_id, None, "session_started", {
            "session_id": session_id,
            "goal": raw_goal,
        })

        try:
            # ── Step 1: Parse intent
            await self._emit(session_id, None, "parsing_intent", {"goal": raw_goal})
            intent = await self.intent_parser.parse(raw_goal)

            # ── Step 2: Build DAG
            await self._emit(session_id, None, "building_dag", {"domain": intent.domain})
            graph = await self.dag_builder.build(intent)
            self.sessions[session_id] = graph

            await self._emit(session_id, None, "dag_ready", {
                "tasks": [t.to_dict() for t in graph.tasks.values()],
                "total_tasks": len(graph.tasks),
                "session_id": session_id,
            })

            # ── Step 3: Execute workflow
            await self.workflow_engine.execute(
                graph=graph,
                container_manager=self.container_mgr,
                event_callback=lambda event: asyncio.create_task(
                    self._emit(session_id, event.task_id, event.event_type, event.data)
                ),
            )

            # ── Step 4: Aggregate and return results
            results = self._aggregate_results(graph)
            await self._emit(session_id, None, "workflow_complete", {
                "results": results,
                "session_id": session_id,
                "total_duration_ms": (time.time() - graph.created_at) * 1000,
            })

        except Exception as e:
            log.error(f"Pipeline error for session {session_id}: {e}", exc_info=True)
            await self._emit(session_id, None, "pipeline_error", {"error": str(e)})

    async def _handle_cancel_workflow(
        self, ws: WebSocketServerProtocol, client_id: str, msg: dict
    ):
        session_id = msg.get("session_id")
        graph = self.sessions.get(session_id)
        if graph:
            for task in graph.tasks.values():
                if task.state in (TaskState.PENDING, TaskState.RUNNING, TaskState.READY):
                    task.state = TaskState.CANCELLED
                    if task.container_id:
                        await self.container_mgr.kill_container(task.container_id)
            await self._emit(session_id, None, "workflow_cancelled", {})

    async def _handle_get_status(
        self, ws: WebSocketServerProtocol, client_id: str, msg: dict
    ):
        session_id = msg.get("session_id")
        graph = self.sessions.get(session_id)
        if not graph:
            await ws.send(json.dumps({"error": "Session not found"}))
            return
        await ws.send(json.dumps({
            "session_id": session_id,
            "tasks": [t.to_dict() for t in graph.tasks.values()],
        }))

    async def _handle_retry_task(
        self, ws: WebSocketServerProtocol, client_id: str, msg: dict
    ):
        session_id = msg.get("session_id")
        task_id = msg.get("task_id")
        graph = self.sessions.get(session_id)
        if graph and task_id in graph.tasks:
            task = graph.tasks[task_id]
            if task.state == TaskState.FAILED:
                task.state = TaskState.PENDING
                task.retry_count += 1
                task.error = None
                await self._emit(session_id, task_id, "task_retrying", {})

    # ── Result Aggregation ────────────────────────────────────────────────────

    def _aggregate_results(self, graph: TaskGraph) -> Dict[str, Any]:
        """
        Collect results from all terminal nodes (nodes with no downstream tasks).
        Terminal nodes = sink nodes in the DAG.
        """
        sink_ids = {
            tid for tid in graph.tasks
            if not graph.edges.get(tid)     # no outgoing edges
        }
        aggregated = {}
        for tid in sink_ids:
            task = graph.tasks[tid]
            if task.state == TaskState.COMPLETE:
                aggregated[tid] = {
                    "description": task.description,
                    "type": task.type.value,
                    "result": task.result,
                    "duration_ms": task.duration_ms(),
                }
        return aggregated

    # ── Event Emission ────────────────────────────────────────────────────────

    async def _emit(
        self,
        session_id: str,
        task_id: Optional[str],
        event_type: str,
        data: Dict[str, Any],
    ):
        """Send a TaskEvent to the connected browser client."""
        event = TaskEvent(
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            data=data,
        )
        ws = self.clients.get(session_id)
        if ws:
            try:
                await ws.send(event.to_json())
            except websockets.exceptions.ConnectionClosed:
                log.warning(f"Could not emit {event_type} — client disconnected")
        log.info(f"[{session_id[:8]}] EVENT: {event_type}" +
                 (f" task={task_id}" if task_id else ""))


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    orchestrator = Orchestrator()
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
