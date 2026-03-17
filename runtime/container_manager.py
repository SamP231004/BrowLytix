"""
================================================================================
  AGENTIC OS BROWSER — CONTAINER RUNTIME
================================================================================
  Manages the full lifecycle of ephemeral nanoservice containers.
  Each task gets its own isolated Docker/Firecracker container that:
    1. Boots from a minimal Alpine base image
    2. Auto-installs required tools via the ToolInstaller
    3. Executes the task payload via an injected task_runner
    4. Returns the structured result
    5. Self-destructs

  Algorithm:
    pull_or_use_cached_image()
    → create_container(resource_limits)
    → inject_task_payload(context)
    → auto_install_tools(tools_required)
    → execute_task_runner()
    → collect_result()
    → destroy_container()
================================================================================
"""

# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiodocker
from aiodocker.containers import DockerContainer

log = logging.getLogger("container_runtime")

def write_unix_file(path: Path, content: str):
    """
    Write files with strict Unix line endings and UTF-8 encoding.
    Prevents CRLF issues when executing scripts inside Linux containers.
    """
    content = content.replace("\r\n", "\n").replace("\r", "")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


# ══════════════════════════════════════════════════════════════════════════════
#  RESOURCE LIMITS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ResourceLimits:
    memory_mb: int = 512
    cpu_count: float = 0.5
    disk_mb: int = 1024
    network_enabled: bool = True
    timeout_s: int = 300

    def to_docker_config(self) -> dict:
        return {
            "Memory":     self.memory_mb * 1024 * 1024,
            "NanoCPUs":   int(self.cpu_count * 1e9),
            "PidsLimit":  200,
        }


TASK_RESOURCE_PROFILES: Dict[str, ResourceLimits] = {
    "web_research":        ResourceLimits(memory_mb=512,  cpu_count=0.5,  network_enabled=True),
    "code_execution":      ResourceLimits(memory_mb=1024, cpu_count=1.0,  network_enabled=True),
    "data_processing":     ResourceLimits(memory_mb=2048, cpu_count=2.0,  network_enabled=True),
    "image_generation":    ResourceLimits(memory_mb=4096, cpu_count=2.0,  network_enabled=True),
    "document_generation": ResourceLimits(memory_mb=512,  cpu_count=0.5,  network_enabled=True),
    "file_operation":      ResourceLimits(memory_mb=256,  cpu_count=0.25, network_enabled=False),
    "api_call":            ResourceLimits(memory_mb=256,  cpu_count=0.25, network_enabled=True),
    "deployment":          ResourceLimits(memory_mb=1024, cpu_count=1.0,  network_enabled=True),
    "reasoning":           ResourceLimits(memory_mb=512,  cpu_count=0.5,  network_enabled=True),
    "summarisation":       ResourceLimits(memory_mb=256,  cpu_count=0.25, network_enabled=True),
}


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL INSTALLER
# ══════════════════════════════════════════════════════════════════════════════

# Canonical map: abstract tool name → install instructions
TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Python ecosystem
    "python3":          {"apk": ["python3", "py3-pip"]},
    "pandas":           {"pip": ["pandas==2.2.2"]},
    "numpy":            {"pip": ["numpy==1.26.4"]},
    "scipy":            {"pip": ["scipy==1.13.0"]},
    "matplotlib":       {"pip": ["matplotlib==3.9.0"]},
    "plotly":           {"pip": ["plotly==5.22.0", "kaleido==0.2.1"]},
    "requests":         {"pip": ["requests==2.32.2"]},
    "beautifulsoup4":   {"pip": ["beautifulsoup4==4.12.3", "lxml==5.2.1"]},
    "playwright":       {"pip": ["playwright==1.44.0"],
                         "post": ["playwright install chromium --with-deps"]},
    "selenium":         {"pip": ["selenium==4.21.0"],
                         "apk": ["chromium", "chromium-chromedriver"]},
    "aiohttp":          {"pip": ["aiohttp==3.9.5"]},
    "httpx":            {"pip": ["httpx==0.27.0"]},
    "sqlalchemy":       {"pip": ["sqlalchemy==2.0.30"]},
    "openpyxl":         {"pip": ["openpyxl==3.1.2"]},
    "pydantic":         {"pip": ["pydantic==2.7.1"]},
    "fastapi":          {"pip": ["fastapi==0.111.0", "uvicorn==0.30.1"]},
    "transformers":     {"pip": ["transformers==4.41.0", "torch==2.3.0"]},
    "scikit-learn":     {"pip": ["scikit-learn==1.5.0"]},
    "openai":           {"pip": ["openai==1.30.1"]},
    "anthropic":        {"pip": ["anthropic==0.28.0"]},
    # System tools
    "git":              {"apk": ["git"]},
    "curl":             {"apk": ["curl"]},
    "ffmpeg":           {"apk": ["ffmpeg"]},
    "pandoc":           {"apk": ["pandoc"]},
    "imagemagick":      {"apk": ["imagemagick"]},
    "ghostscript":      {"apk": ["ghostscript"]},
    # Node.js
    "node":             {"apk": ["nodejs", "npm"]},
    "typescript":       {"npm_global": ["typescript"]},
    "puppeteer":        {"npm": ["puppeteer@22.0.0"],
                         "post": ["npx puppeteer browsers install chrome"]},
    # Cloud CLIs
    "awscli":           {"pip": ["awscli==1.33.0"]},
    "gcloud":           {"post": ["curl https://sdk.cloud.google.com | bash"]},
    "docker":           {"apk": ["docker-cli"]},
    # Data
    "sqlite":           {"apk": ["sqlite"]},
    "postgresql":       {"apk": ["postgresql-client"]},
    "redis":            {"apk": ["redis"]},
}


class ToolInstaller:
    """
    Generates an optimised install script for a container
    given a list of required tool names.

    Algorithm:
      1. Resolve tool names → install commands (O(n) registry lookup)
      2. Batch APK packages → one `apk add` call (minimise layers)
      3. Batch PIP packages → one `pip install` call
      4. Batch NPM globals  → one `npm install -g` call
      5. Append post-install commands in declaration order
    """

    def generate_install_script(self, tools: List[str]) -> str:
        apk_pkgs: List[str] = []
        pip_pkgs: List[str] = []
        npm_pkgs: List[str] = []
        npm_global_pkgs: List[str] = []
        post_cmds: List[str] = []
        unknown: List[str] = []

        for tool in tools:
            spec = TOOL_REGISTRY.get(tool.lower())
            if not spec:
                unknown.append(tool)
                continue
            apk_pkgs.extend(spec.get("apk", []))
            pip_pkgs.extend(spec.get("pip", []))
            npm_pkgs.extend(spec.get("npm", []))
            npm_global_pkgs.extend(spec.get("npm_global", []))
            post_cmds.extend(spec.get("post", []))

        if unknown:
            log.warning(f"Unknown tools (skipped): {unknown}")

        lines = ["#!/bin/sh", "set -e"]

        # Always update apk index first
        if apk_pkgs:
            unique_apk = list(dict.fromkeys(apk_pkgs))  # deduplicate, preserve order
            lines.append(f"apk add --no-cache --quiet {' '.join(unique_apk)}")

        if pip_pkgs:
            unique_pip = list(dict.fromkeys(pip_pkgs))
            lines.append(f"pip install --quiet --no-cache-dir {' '.join(unique_pip)}")

        # Install node first if npm packages are needed
        if npm_pkgs or npm_global_pkgs:
            if "nodejs" not in apk_pkgs:
                lines.append("apk add --no-cache --quiet nodejs npm")
            if npm_pkgs:
                unique_npm = list(dict.fromkeys(npm_pkgs))
                lines.append(f"npm install --silent {' '.join(unique_npm)}")
            if npm_global_pkgs:
                unique_npm_g = list(dict.fromkeys(npm_global_pkgs))
                lines.append(f"npm install -g --silent {' '.join(unique_npm_g)}")

        for cmd in post_cmds:
            lines.append(cmd)

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  TASK RUNNER SCRIPT (injected into every container)
# ══════════════════════════════════════════════════════════════════════════════

TASK_RUNNER_SCRIPT = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
AGENTIC OS — CONTAINER TASK RUNNER
================================================================================

Generic execution runtime for nanoservice tasks.

Workflow:
1. Load /task/input.json
2. If generated_code.py exists → execute it
3. Otherwise fallback to lightweight handlers
4. Write result to /task/output.json
================================================================================
"""

import json
import os
import subprocess

TASK_DIR = "/task"
INPUT_FILE = os.path.join(TASK_DIR, "input.json")
OUTPUT_FILE = os.path.join(TASK_DIR, "output.json")
GENERATED_CODE_FILE = os.path.join(TASK_DIR, "generated_code.py")


# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------

def load_input():
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError("input.json not found")

    with open(INPUT_FILE) as f:
        return json.load(f)


def write_output(result):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)


def run_generated_code():
    """Execute generated Python code if present"""
    if not os.path.exists(GENERATED_CODE_FILE):
        return None

    try:
        proc = subprocess.run(
            ["python3", GENERATED_CODE_FILE],
            capture_output=True,
            text=True,
            timeout=180
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if stdout:
            try:
                return json.loads(stdout)
            except Exception:
                return {"stdout": stdout, "stderr": stderr}

        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode
        }

    except subprocess.TimeoutExpired:
        return {"error": "Generated code execution timed out"}

    except Exception as e:
        return {"error": str(e)}


# ------------------------------------------------------------------------------
# Minimal Fallback Handlers
# ------------------------------------------------------------------------------

def run_web_research(description):
    return {
        "task": "web_research",
        "query": description,
        "note": "Web research handler not implemented"
    }


# ------------------------------------------------------------------------------
# Task Router
# ------------------------------------------------------------------------------

def execute_task(ctx):

    task_type = ctx.get("task_type")
    description = ctx.get("description", "")

    # FIRST: run generated code if available
    generated_result = run_generated_code()
    if generated_result is not None:
        return generated_result

    # FALLBACK handlers
    if task_type == "web_research":
        return run_web_research(description)

    return {
        "task_type": task_type,
        "status": "executed",
        "note": "No generated code provided"
    }


# ------------------------------------------------------------------------------
# Entry
# ------------------------------------------------------------------------------

def main():

    try:
        ctx = load_input()
        result = execute_task(ctx)

    except Exception as e:
        result = {"error": str(e)}

    write_output(result)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
'''

# ══════════════════════════════════════════════════════════════════════════════
#  CONTAINER MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ContainerManager:
    """
    Manages ephemeral Docker containers for task execution.

    Full lifecycle: create → bootstrap → inject → execute → collect → destroy
    Uses aiodocker for async container management.
    """

    BASE_IMAGE = "python:3.12-alpine"
    TASK_DIR = "/task"

    def __init__(self):
        self.installer = ToolInstaller()
        self._docker: Optional[aiodocker.Docker] = None
        self._active_containers: Dict[str, str] = {}   # task_id → container_id

    async def _get_docker(self) -> aiodocker.Docker:
        if self._docker is None:
            self._docker = aiodocker.Docker()
        return self._docker

    async def run_task(
        self,
        task: Any,
        context: Any,
        router: Any,
    ) -> Any:
        """
        Full container lifecycle for one task.
        Returns the task result dict.
        """
        container_id = None
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix=f"agentic_task_{task.id}_")
            tmp_path = Path(tmp_dir)

            # ── Step 1: Prepare task payload ─────────────────────────────────
            payload = {
                "task_id": context.task_id,
                "task_type": context.task_type,
                "description": context.description,
                "tools_required": context.tools_required,
                "preferred_llm": context.preferred_llm,
                "upstream_results": context.upstream_results,
                "session_id": context.session_id,
            }

            # write input.json
            write_unix_file(
                tmp_path / "input.json",
                json.dumps(payload, indent=2, default=str)
            )

            # write task_runner.py
            write_unix_file(
                tmp_path / "task_runner.py",
                TASK_RUNNER_SCRIPT
            )

            # ── Step 2: Generate install script ──────────────────────────────
            install_script = self.installer.generate_install_script(task.tools_required)
            install_path = tmp_path / "install.sh"
            write_unix_file(
                install_path,
                install_script
            )

            # make executable
            os.chmod(install_path, 0o755)

            # ── Step 3: LLM generates task-specific code if needed ────────────
            if task.type.value in ("code_execution", "data_processing"):
                generated_code = await self._generate_task_code(task, context, router)
                if generated_code:
                    write_unix_file(
                    tmp_path / "generated_code.py",
                    generated_code
                )

            # ── Step 4: Spin up container ────────────────────────────────────
            resource_limits = TASK_RESOURCE_PROFILES.get(
                task.type.value,
                ResourceLimits()
            )
            container_id = await self._create_and_run_container(
                task_id=task.id,
                tmp_dir=tmp_dir,
                resource_limits=resource_limits,
                network_enabled=resource_limits.network_enabled,
            )
            task.container_id = container_id
            self._active_containers[task.id] = container_id

            # ── Step 5: Collect result ────────────────────────────────────────
            result = await self._collect_result(container_id, tmp_path)
            return result

        except Exception as e:
            log.error(f"Container task {task.id} failed: {e}", exc_info=True)
            raise

        finally:
            # ── Step 6: Always destroy container ─────────────────────────────
            if container_id:
                await self.kill_container(container_id)
                self._active_containers.pop(task.id, None)
            # Clean up tmp dir
            if tmp_dir:
                import shutil
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    async def _create_and_run_container(
        self,
        task_id: str,
        tmp_dir: str,
        resource_limits: ResourceLimits,
        network_enabled: bool,
    ) -> str:
        """Create container, run install + task_runner, return container ID."""
        docker = await self._get_docker()

        container_name = f"agentic-task-{task_id}-{uuid.uuid4().hex[:8]}"

        # Build the command: install tools then run task runner
        cmd = [
            "sh",
            "-c",
            "chmod +x /task/install.sh && /task/install.sh && python3 /task/task_runner.py"
        ]

        host_path = str(Path(tmp_dir).resolve()).replace("\\", "/")

        config = {
            "Image": self.BASE_IMAGE,
            "Cmd": cmd,
            "WorkingDir": self.TASK_DIR,
            "NetworkDisabled": not network_enabled,
            "HostConfig": {
                **resource_limits.to_docker_config(),
                "Binds": [f"{host_path}:{self.TASK_DIR}:rw"],
                "AutoRemove": False,
                "ReadonlyRootfs": False,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "CapAdd": ["CHOWN", "SETUID", "SETGID"],
            },
            "Env": [
                "PYTHONDONTWRITEBYTECODE=1",
                "PYTHONUNBUFFERED=1",
                "PIP_NO_CACHE_DIR=1",
            ],
        }

        container: DockerContainer = await docker.containers.create(
            config=config,
            name=container_name,
        )
        await container.start()
        log.info(f"Container started: {container_name} (id={container.id[:12]})")
        return container.id

    async def _collect_result(
        self, container_id: str, tmp_path: Path
    ) -> Any:
        """
        Wait for container to finish, then read output.json from shared volume.
        Falls back to stdout if output.json is missing.
        """
        docker = await self._get_docker()
        container = await docker.containers.get(container_id)

        # Wait for completion (with timeout)
        wait_result = await container.wait()
        exit_code = wait_result.get("StatusCode", -1)

        # Read stdout/stderr for diagnostics
        logs = await container.log(stdout=True, stderr=True)
        log_text = "".join(logs)

        output_path = tmp_path / "output.json"
        if output_path.exists():
            try:
                return json.loads(output_path.read_text())
            except json.JSONDecodeError as e:
                log.warning(f"Invalid JSON in output.json: {e}")
                return {"raw_output": output_path.read_text()[:5000]}

        # Fall back to stdout
        if log_text.strip():
            try:
                # Try to parse last JSON object from stdout
                lines = log_text.strip().split("\n")
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("{"):
                        return json.loads(line)
            except Exception:
                pass
            return {"stdout": log_text[:5000], "exit_code": exit_code}

        return {
            "exit_code": exit_code,
            "error": "No output produced",
            "logs": log_text[:1000],
        }

    async def _generate_task_code(
        self, task: Any, context: Any, router: Any
    ) -> Optional[str]:
        """
        Use LLM to generate the Python code for code_execution / data_processing tasks.
        The generated code is saved to /task/generated_code.py inside the container.
        """
        upstream_summary = json.dumps(
            {k: str(v)[:300] for k, v in context.upstream_results.items()},
            indent=2
        )
        prompt = f"""Write clean, executable Python 3 code for this task:

Task: {task.description}
Tools available: {task.tools_required}
Upstream data:
{upstream_summary}

RULES:
- Write ONLY the Python code. No markdown. No explanation.
- Save final output as: result = {{...}}
- Print the result as JSON at the end: import json; print(json.dumps(result, default=str))
- Handle exceptions gracefully
- Use only the specified tools"""

        try:
            code = await router.call_llm(
                prompt=prompt,
                task_type=task.type.value,
                model_preference=task.preferred_llm.value,
                max_tokens=3000,
                temperature=0.1,
            )
            # Strip markdown fences if present
            code = code.strip()
            if code.startswith("```"):
                lines = code.split("\n")
                code = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            return code
        except Exception as e:
            log.warning(f"Code generation failed for task {task.id}: {e}")
            return None

    async def kill_container(self, container_id: str) -> None:
        """Force-kill and remove a container."""
        try:
            docker = await self._get_docker()
            container = await docker.containers.get(container_id)
            try:
                await container.kill()
            except Exception:
                pass
            try:
                await container.delete(force=True)
                log.info(f"Container destroyed: {container_id[:12]}")
            except Exception as e:
                log.warning(f"Container delete failed: {e}")
        except Exception as e:
            log.warning(f"Could not kill container {container_id[:12]}: {e}")

    async def cleanup_all(self) -> None:
        """Emergency cleanup — kill all active containers."""
        for task_id, container_id in list(self._active_containers.items()):
            await self.kill_container(container_id)
        self._active_containers.clear()

    async def close(self) -> None:
        await self.cleanup_all()
        if self._docker:
            await self._docker.close()
