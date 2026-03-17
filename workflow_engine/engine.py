"""
================================================================================
  AGENTIC OS BROWSER — WORKFLOW ENGINE
================================================================================
  Executes a validated TaskGraph by:
    - Scheduling tasks in topological order (Kahn's BFS)
    - Running independent tasks in parallel (asyncio.gather)
    - Managing data flow between tasks (dependency injection)
    - Handling retries with exponential backoff
    - Streaming events via callback

  Algorithm:
    1.  Compute initial in-degree map from DAG
    2.  Seed ready_queue with all zero-in-degree nodes
    3.  While ready_queue not empty:
        a. Drain ready_queue → batch of ready tasks
        b. asyncio.gather(*[execute(t) for t in batch])
        c. For each completed task, decrement in-degree of dependents
        d. Enqueue newly ready dependents
    4.  Inject upstream results as input context for each task
    5.  Handle failure: retry up to max_retries, then fail the session
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger("workflow_engine")


# ── Forward references (avoid circular imports) ───────────────────────────────
# These types are defined in orchestrator.py; imported lazily at runtime.
# Type hints below are strings to avoid import-time circular dependency.


@dataclass
class ExecutionContext:
    """Execution context injected into each task container."""
    task_id: str
    task_type: str
    description: str
    tools_required: List[str]
    preferred_llm: str
    upstream_results: Dict[str, Any]   # task_id → result from completed deps
    session_id: str


class WorkflowEngine:
    """
    Parallel DAG executor using asyncio.
    Maximises concurrency while respecting data dependencies.
    """

    MAX_CONCURRENT_TASKS = 8            # global concurrency cap
    TASK_TIMEOUT_S = 600                # 10 min per task hard limit
    RETRY_BACKOFF_BASE = 2.0            # seconds
    RETRY_BACKOFF_MAX = 30.0            # seconds cap

    def __init__(self, llm_router: Any):
        self.router = llm_router
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_TASKS)

    async def execute(
        self,
        graph: Any,                     # TaskGraph
        container_manager: Any,         # ContainerManager
        event_callback: Callable,
    ) -> None:
        """
        Main execution loop — parallel topological BFS.

        Time complexity: O(V + E) scheduling + O(max_parallelism) wall-clock.
        """
        from orchestrator.orchestrator import TaskState, TaskEvent

        # ── Build in-degree map (number of unresolved dependencies per task)
        in_degree: Dict[str, int] = {
            tid: len(task.inputs)
            for tid, task in graph.tasks.items()
        }

        # ── Initialise ready queue with zero-in-degree tasks
        ready_queue: asyncio.Queue = asyncio.Queue()
        for tid, deg in in_degree.items():
            if deg == 0:
                graph.tasks[tid].state = TaskState.READY
                await ready_queue.put(tid)

        completed_tasks: Set[str] = set()
        running_futures: Dict[str, asyncio.Task] = {}

        async def on_task_done(task_id: str, success: bool):
            """Callback when a task finishes — unblock dependents."""
            completed_tasks.add(task_id)
            if success:
                # Decrement in-degree for all downstream tasks
                for downstream_id in graph.edges.get(task_id, set()):
                    in_degree[downstream_id] -= 1
                    if in_degree[downstream_id] == 0:
                        graph.tasks[downstream_id].state = TaskState.READY
                        await ready_queue.put(downstream_id)

        # ── Main scheduling loop
        while len(completed_tasks) < len(graph.tasks):
            # Drain the ready queue and launch all available tasks
            batch: List[str] = []
            while not ready_queue.empty():
                batch.append(await ready_queue.get())

            if not batch and not running_futures:
                # No ready tasks and nothing running — stuck (shouldn't happen on valid DAG)
                log.error("Workflow stalled — no ready tasks and no running tasks")
                break

            # Launch batch tasks concurrently
            for task_id in batch:
                if task_id in running_futures:
                    continue
                task = graph.tasks[task_id]
                ctx = self._build_context(task, graph)

                fut = asyncio.create_task(
                    self._execute_task_with_retry(
                        task=task,
                        context=ctx,
                        container_manager=container_manager,
                        event_callback=event_callback,
                        on_done=on_task_done,
                        graph=graph,
                    ),
                    name=f"task_{task_id}",
                )
                running_futures[task_id] = fut

            # Wait for at least one task to complete before re-checking queue
            if running_futures:
                done, _ = await asyncio.wait(
                    running_futures.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for future in done:
                    # Clean up completed futures
                    finished_id = next(
                        tid for tid, f in running_futures.items() if f == future
                    )
                    del running_futures[finished_id]

            # Yield to event loop to allow queue updates
            await asyncio.sleep(0)

        log.info(f"Workflow complete: {len(completed_tasks)}/{len(graph.tasks)} tasks done")

    def _build_context(self, task: Any, graph: Any) -> ExecutionContext:
        """Inject upstream task results as context for the current task."""
        upstream_results = {}
        for dep_id in task.inputs:
            dep_task = graph.tasks.get(dep_id)
            if dep_task and dep_task.result is not None:
                upstream_results[dep_id] = dep_task.result

        return ExecutionContext(
            task_id=task.id,
            task_type=task.type.value,
            description=task.description,
            tools_required=task.tools_required,
            preferred_llm=task.preferred_llm.value,
            upstream_results=upstream_results,
            session_id=graph.session_id,
        )

    async def _execute_task_with_retry(
        self,
        task: Any,
        context: ExecutionContext,
        container_manager: Any,
        event_callback: Callable,
        on_done: Callable,
        graph: Any,
    ) -> None:
        """Execute a task with exponential backoff retry logic."""
        from orchestrator.orchestrator import TaskState

        for attempt in range(task.max_retries + 1):
            try:
                await self._execute_single_task(
                    task=task,
                    context=context,
                    container_manager=container_manager,
                    event_callback=event_callback,
                )
                await on_done(task.id, success=True)
                return

            except asyncio.TimeoutError:
                task.error = f"Task timed out after {self.TASK_TIMEOUT_S}s"
                log.error(f"Task {task.id} timed out (attempt {attempt+1})")

            except Exception as e:
                task.error = str(e)
                log.error(f"Task {task.id} failed (attempt {attempt+1}): {e}")

            if attempt < task.max_retries:
                wait = min(
                    self.RETRY_BACKOFF_BASE * (2 ** attempt),
                    self.RETRY_BACKOFF_MAX
                )
                task.state = TaskState.RETRYING
                task.retry_count += 1
                event_callback(type("E", (), {
                    "task_id": task.id, "event_type": "task_retrying",
                    "data": {"attempt": attempt + 1, "wait_s": wait}
                })())
                await asyncio.sleep(wait)
            else:
                task.state = TaskState.FAILED
                event_callback(type("E", (), {
                    "task_id": task.id, "event_type": "task_failed",
                    "data": {"error": task.error, "attempts": attempt + 1}
                })())
                await on_done(task.id, success=False)

    async def _execute_single_task(
        self,
        task: Any,
        context: ExecutionContext,
        container_manager: Any,
        event_callback: Callable,
    ) -> None:
        """
        Execute one task inside a container.
        Uses semaphore to enforce MAX_CONCURRENT_TASKS.
        """
        from orchestrator.orchestrator import TaskState

        async with self._semaphore:
            task.state = TaskState.RUNNING
            task.started_at = time.time()

            event_callback(type("E", (), {
                "task_id": task.id, "event_type": "task_started",
                "data": {
                    "description": task.description,
                    "tools": task.tools_required,
                    "run_locally": task.run_locally,
                }
            })())

            try:
                result = await asyncio.wait_for(
                    container_manager.run_task(
                        task=task,
                        context=context,
                        router=self.router,
                    ),
                    timeout=self.TASK_TIMEOUT_S,
                )
                task.result = result
                task.state = TaskState.COMPLETE
                task.completed_at = time.time()

                event_callback(type("E", (), {
                    "task_id": task.id, "event_type": "task_complete",
                    "data": {
                        "duration_ms": task.duration_ms(),
                        "result_preview": str(result)[:200] if result else None,
                    }
                })())

            except Exception:
                task.completed_at = time.time()
                raise
