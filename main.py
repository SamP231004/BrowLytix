"""
================================================================================
  AGENTIC OS BROWSER — MAIN ENTRY POINT
================================================================================
  Starts all services:
    - Orchestrator daemon (WebSocket server on localhost:7771)
    - Resource monitor background task
    - Vault integrity check on startup
    - Graceful shutdown handler
================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.orchestrator import Orchestrator
from vault.vault import Vault
from hybrid_planner.planner import ResourceMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agentic_os.log"),
    ]
)
log = logging.getLogger("main")

BANNER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║           █████╗  ██████╗ ███████╗███╗  ██╗████████╗██╗ ██████╗            ║
║          ██╔══██╗██╔════╝ ██╔════╝████╗ ██║╚══██╔══╝██║██╔════╝            ║
║          ███████║██║  ███╗█████╗  ██╔██╗██║   ██║   ██║██║                 ║
║          ██╔══██║██║   ██║██╔══╝  ██║╚████║   ██║   ██║██║                 ║
║          ██║  ██║╚██████╔╝███████╗██║ ╚███║   ██║   ██║╚██████╗            ║
║          ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚══╝   ╚═╝   ╚═╝ ╚═════╝           ║
║                                                                              ║
║                      OS  BROWSER  v1.0.0                                    ║
║            Chromium Fork · Multi-LLM · Containerised Nanoservices           ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


async def startup_checks():
    """Run pre-flight checks before starting services."""
    log.info("Running startup checks...")

    # 1. Check Docker availability
    try:
        import aiodocker
        docker = aiodocker.Docker()
        info = await docker.system.info()
        log.info(f"Docker: available (version={info.get('ServerVersion', 'unknown')})")
        await docker.close()
    except Exception as e:
        log.warning(f"Docker not available: {e}. Container tasks will fail.")

    # 2. Check Ollama availability
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get("http://localhost:11434/api/version", timeout=aiohttp.ClientTimeout(total=2)) as r:
                if r.status == 200:
                    data = await r.json()
                    log.info(f"Ollama: available (version={data.get('version', 'unknown')})")
    except Exception:
        log.warning("Ollama not running — local LLMs (Llama/Mistral/Qwen) unavailable")

    # 3. Check vault
    try:
        vault = Vault()
        keys = vault.list_keys()
        log.info(f"Vault: OK ({len(keys)} stored keys)")
    except Exception as e:
        log.warning(f"Vault check failed: {e}")

    # 4. Check system resources
    monitor = ResourceMonitor()
    resources = monitor.get_resources()
    log.info(
        f"System: CPU={resources.cpu_percent:.1f}% "
        f"RAM={resources.memory_percent:.1f}% "
        f"FreeMem={resources.available_memory_gb:.1f}GB"
    )
    log.info("Startup checks complete.")


async def main():
    print(BANNER)
    await startup_checks()

    orchestrator = Orchestrator()

    # ── Graceful shutdown ─────────────────────────────────────────────────
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        log.info("Shutdown signal received — stopping gracefully...")
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            pass   # Windows doesn't support add_signal_handler

    log.info("Starting Orchestrator WebSocket server on ws://localhost:7771")
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
