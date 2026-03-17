# Agentic OS Browser

> An open-source Chromium fork that replaces the URL bar with an AI-powered intent engine.
> Users type a goal — the browser autonomously decomposes, containerises, routes to the best LLM, executes, and returns results.

## Quick Start
```bash
pip install -r requirements.txt
python main.py          # starts orchestrator on ws://localhost:7771
python tests/test_all_modules.py   # 35 unit tests
```

## Modules
| Module | File | Description |
|--------|------|-------------|
| Orchestrator | orchestrator/orchestrator.py | Intent parser, DAG builder, WebSocket server |
| Multi-LLM Router | router/router.py | Weighted model selection across 6 LLMs |
| Workflow Engine | workflow_engine/engine.py | Parallel DAG executor (asyncio BFS) |
| Container Runtime | runtime/container_manager.py | Docker lifecycle + tool auto-installer |
| Hybrid Planner | hybrid_planner/planner.py | Local vs cloud decision + PII scrubber |
| Security Vault | vault/vault.py | AES-256-GCM encrypted credential store |
| Browser Bridge | browser_bridge/bridge.py | Chromium ↔ Orchestrator WebSocket relay |

## Architecture
See `ARCHITECTURE.docx` for the full technical design document.

## License
Apache 2.0 (orchestration backend) · BSD (browser fork)
