"""
================================================================================
  AGENTIC OS BROWSER — BROWSER ↔ ORCHESTRATOR WEBSOCKET BRIDGE
================================================================================
  This module has TWO parts:

  PART A: Python-side bridge server (injected into Chromium as a native host)
  PART B: JavaScript/TypeScript client code for the Chromium Intent Bar UI

  The bridge allows the Chromium renderer process to communicate with
  the Python Orchestrator daemon via WebSocket on localhost:7771.

  Architecture:
    Chromium (Renderer Process)
      └─ Intent Bar UI (TypeScript)
           └─ WebSocket → ws://localhost:7771
                └─ Orchestrator (Python daemon)
                     ├─ DAG Builder
                     ├─ Multi-LLM Router
                     ├─ Workflow Engine
                     └─ Container Runtime
================================================================================
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
#  PART A: PYTHON NATIVE MESSAGING HOST
#  (launched by Chromium when the extension is activated)
# ══════════════════════════════════════════════════════════════════════════════

import asyncio
import json
import logging
import struct
import sys
from typing import Any, Dict, Optional

import websockets

log = logging.getLogger("browser_bridge")


class NativeMessagingHost:
    """
    Chromium Native Messaging Host.

    Chromium sends messages via stdin and reads from stdout.
    Message format: 4-byte little-endian length prefix + JSON payload.
    This host acts as a relay, forwarding messages to the Orchestrator WebSocket.
    """

    ORCHESTRATOR_URL = "ws://localhost:7771"

    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    async def run(self):
        """Main loop: read from Chromium stdin, forward to Orchestrator."""
        log.info("Native messaging host started")
        try:
            async with websockets.connect(self.ORCHESTRATOR_URL) as ws:
                self._ws = ws
                # Run stdin reader and WebSocket reader concurrently
                await asyncio.gather(
                    self._read_from_chrome(ws),
                    self._read_from_orchestrator(ws),
                )
        except ConnectionRefusedError:
            log.error(
                "Cannot connect to Orchestrator on ws://localhost:7771. "
                "Ensure the Orchestrator daemon is running."
            )
            self._send_to_chrome({
                "error": "orchestrator_offline",
                "message": "Orchestrator daemon is not running. "
                           "Please start it with: python -m orchestrator.orchestrator",
            })

    async def _read_from_chrome(self, ws: websockets.WebSocketClientProtocol):
        """Read 4-byte-prefixed messages from Chromium stdin."""
        loop = asyncio.get_event_loop()
        while True:
            # Read 4-byte length header (non-blocking)
            raw_len = await loop.run_in_executor(None, sys.stdin.buffer.read, 4)
            if len(raw_len) == 0:
                break   # Chrome closed the pipe
            msg_len = struct.unpack("<I", raw_len)[0]
            raw_msg = await loop.run_in_executor(None, sys.stdin.buffer.read, msg_len)
            if not raw_msg:
                break
            try:
                message = json.loads(raw_msg.decode("utf-8"))
                log.debug(f"Chrome → Orchestrator: {message.get('action')}")
                await ws.send(json.dumps(message))
            except json.JSONDecodeError as e:
                log.error(f"Invalid JSON from Chrome: {e}")

    async def _read_from_orchestrator(self, ws: websockets.WebSocketClientProtocol):
        """Forward Orchestrator events back to Chromium."""
        async for raw_message in ws:
            try:
                message = json.loads(raw_message)
                self._send_to_chrome(message)
            except json.JSONDecodeError as e:
                log.error(f"Invalid JSON from Orchestrator: {e}")

    def _send_to_chrome(self, message: Dict[str, Any]):
        """Write 4-byte-prefixed JSON message to Chromium stdout."""
        encoded = json.dumps(message).encode("utf-8")
        length_prefix = struct.pack("<I", len(encoded))
        sys.stdout.buffer.write(length_prefix + encoded)
        sys.stdout.buffer.flush()


# ══════════════════════════════════════════════════════════════════════════════
#  PART B: TYPESCRIPT INTENT BAR UI
#  Save as: browser/src/intent_bar/intent_bar.ts
#  Compile with: tsc intent_bar.ts --target ES2020
# ══════════════════════════════════════════════════════════════════════════════

INTENT_BAR_TYPESCRIPT = """
// =============================================================================
//  AGENTIC OS BROWSER — INTENT BAR UI
//  Replaces the Chromium OmniBox with an AI-powered intent input field.
// =============================================================================

interface TaskNode {
  id: string;
  type: string;
  description: string;
  state: 'pending' | 'ready' | 'running' | 'complete' | 'failed' | 'cancelled';
  inputs: string[];
  duration_ms?: number;
}

interface OrchestratorEvent {
  session_id: string;
  task_id?: string;
  event_type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

class IntentBar {
  private ws: WebSocket | null = null;
  private currentSessionId: string | null = null;
  private taskMap: Map<string, TaskNode> = new Map();
  private readonly ORCHESTRATOR_URL = 'ws://localhost:7771';

  // ── DOM elements ────────────────────────────────────────────────────────
  private readonly inputEl: HTMLInputElement;
  private readonly submitBtn: HTMLButtonElement;
  private readonly cancelBtn: HTMLButtonElement;
  private readonly statusEl: HTMLDivElement;
  private readonly dagPanel: HTMLDivElement;
  private readonly progressBar: HTMLDivElement;

  constructor() {
    this.inputEl   = document.getElementById('intent-input')   as HTMLInputElement;
    this.submitBtn = document.getElementById('intent-submit')  as HTMLButtonElement;
    this.cancelBtn = document.getElementById('intent-cancel')  as HTMLButtonElement;
    this.statusEl  = document.getElementById('intent-status')  as HTMLDivElement;
    this.dagPanel  = document.getElementById('dag-panel')      as HTMLDivElement;
    this.progressBar = document.getElementById('progress-bar') as HTMLDivElement;

    this.attachEventListeners();
    this.connectWebSocket();
  }

  private connectWebSocket(): void {
    this.ws = new WebSocket(this.ORCHESTRATOR_URL);

    this.ws.onopen = () => {
      this.setStatus('Connected to Orchestrator', 'success');
      this.submitBtn.disabled = false;
    };

    this.ws.onclose = () => {
      this.setStatus('Orchestrator offline — retrying...', 'error');
      this.submitBtn.disabled = true;
      setTimeout(() => this.connectWebSocket(), 3000);
    };

    this.ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data) as OrchestratorEvent;
        this.handleEvent(msg);
      } catch (e) {
        console.error('Invalid JSON from Orchestrator:', e);
      }
    };
  }

  private attachEventListeners(): void {
    this.submitBtn.addEventListener('click', () => this.executeGoal());
    this.cancelBtn.addEventListener('click', () => this.cancelWorkflow());
    this.inputEl.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.executeGoal();
      }
    });
  }

  private executeGoal(): void {
    const goal = this.inputEl.value.trim();
    if (!goal || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    // Clear previous state
    this.taskMap.clear();
    this.dagPanel.innerHTML = '';
    this.progressBar.style.width = '0%';

    const message = { action: 'execute_goal', goal };
    this.ws.send(JSON.stringify(message));
    this.setStatus('Parsing intent...', 'loading');
    this.submitBtn.disabled = true;
    this.cancelBtn.style.display = 'inline-block';
  }

  private cancelWorkflow(): void {
    if (!this.ws || !this.currentSessionId) return;
    this.ws.send(JSON.stringify({
      action: 'cancel_workflow',
      session_id: this.currentSessionId,
    }));
  }

  private handleEvent(event: OrchestratorEvent): void {
    const { event_type, data, session_id, task_id } = event;

    switch (event_type) {
      case 'session_started':
        this.currentSessionId = session_id;
        this.setStatus(`Session ${session_id.slice(0, 8)} started`, 'info');
        break;

      case 'parsing_intent':
        this.setStatus('Analysing intent...', 'loading');
        break;

      case 'building_dag':
        this.setStatus(`Building workflow (domain: ${data.domain})`, 'loading');
        break;

      case 'dag_ready': {
        const tasks = data.tasks as TaskNode[];
        tasks.forEach(t => this.taskMap.set(t.id, t));
        this.renderDAG(tasks);
        this.setStatus(`Workflow ready: ${tasks.length} tasks`, 'info');
        break;
      }

      case 'task_started':
        if (task_id) {
          const task = this.taskMap.get(task_id);
          if (task) {
            task.state = 'running';
            this.updateTaskUI(task_id, 'running');
          }
        }
        break;

      case 'task_complete':
        if (task_id) {
          const task = this.taskMap.get(task_id);
          if (task) {
            task.state = 'complete';
            task.duration_ms = data.duration_ms as number;
            this.updateTaskUI(task_id, 'complete', `${Math.round(task.duration_ms)}ms`);
            this.updateProgress();
          }
        }
        break;

      case 'task_failed':
        if (task_id) {
          this.updateTaskUI(task_id, 'failed', data.error as string);
        }
        break;

      case 'workflow_complete':
        this.progressBar.style.width = '100%';
        this.setStatus('Workflow complete!', 'success');
        this.submitBtn.disabled = false;
        this.cancelBtn.style.display = 'none';
        this.renderResults(data.results as Record<string, unknown>);
        break;

      case 'pipeline_error':
        this.setStatus(`Error: ${data.error}`, 'error');
        this.submitBtn.disabled = false;
        break;
    }
  }

  private renderDAG(tasks: TaskNode[]): void {
    this.dagPanel.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'dag-grid';

    tasks.forEach(task => {
      const card = document.createElement('div');
      card.id = `task-${task.id}`;
      card.className = `task-card state-${task.state}`;
      card.innerHTML = `
        <div class="task-id">${task.id}</div>
        <div class="task-type badge type-${task.type}">${task.type}</div>
        <div class="task-description">${task.description}</div>
        <div class="task-state">${task.state}</div>
      `;
      grid.appendChild(card);
    });

    this.dagPanel.appendChild(grid);
  }

  private updateTaskUI(taskId: string, state: string, detail?: string): void {
    const card = document.getElementById(`task-${taskId}`);
    if (!card) return;
    card.className = `task-card state-${state}`;
    const stateEl = card.querySelector('.task-state');
    if (stateEl) stateEl.textContent = detail ? `${state} — ${detail}` : state;
  }

  private updateProgress(): void {
    const total = this.taskMap.size;
    const done = Array.from(this.taskMap.values())
      .filter(t => t.state === 'complete').length;
    const pct = total > 0 ? (done / total) * 100 : 0;
    this.progressBar.style.width = `${pct}%`;
    this.setStatus(`Running: ${done}/${total} tasks complete`, 'loading');
  }

  private renderResults(results: Record<string, unknown>): void {
    const resultPanel = document.getElementById('result-panel');
    if (!resultPanel) return;
    resultPanel.innerHTML = '';
    Object.entries(results).forEach(([taskId, result]) => {
      const div = document.createElement('div');
      div.className = 'result-item';
      div.innerHTML = `
        <h3>${taskId}</h3>
        <pre>${JSON.stringify(result, null, 2)}</pre>
      `;
      resultPanel.appendChild(div);
    });
  }

  private setStatus(message: string, type: 'info' | 'loading' | 'success' | 'error'): void {
    this.statusEl.textContent = message;
    this.statusEl.className = `status status-${type}`;
  }
}

// Bootstrap
document.addEventListener('DOMContentLoaded', () => {
  (window as Record<string, unknown>)['intentBar'] = new IntentBar();
});
"""

# ══════════════════════════════════════════════════════════════════════════════
#  PART C: INTENT BAR HTML TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

INTENT_BAR_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Agentic OS Browser</title>
  <style>
    :root {
      --bg: #0D1B2A; --surface: #1A2B3C; --accent: #1A73E8;
      --success: #1A7A4A; --error: #C0392B; --text: #E8F0FE;
      --muted: #8899AA; --border: #2A3B4C;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, sans-serif; }

    #intent-container {
      position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 12px 20px; display: flex; align-items: center; gap: 12px;
    }
    #intent-input {
      flex: 1; background: var(--bg); color: var(--text);
      border: 1px solid var(--border); border-radius: 8px;
      padding: 10px 16px; font-size: 15px; outline: none;
      transition: border-color 0.2s;
    }
    #intent-input:focus { border-color: var(--accent); }
    #intent-input::placeholder { color: var(--muted); }

    button {
      padding: 10px 20px; border-radius: 8px; border: none;
      font-size: 14px; font-weight: 600; cursor: pointer; transition: opacity 0.2s;
    }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    #intent-submit { background: var(--accent); color: white; }
    #intent-cancel { background: var(--error); color: white; display: none; }

    #intent-status {
      font-size: 13px; padding: 4px 10px; border-radius: 6px;
      white-space: nowrap;
    }
    .status-loading { background: #1A3B5C; color: #64B5F6; }
    .status-success { background: #1A3B2A; color: #81C784; }
    .status-error   { background: #3B1A1A; color: #EF9A9A; }
    .status-info    { background: #1A2B3C; color: var(--muted); }

    #progress-container {
      position: fixed; top: 62px; left: 0; right: 0; height: 3px;
      background: var(--border); z-index: 9998;
    }
    #progress-bar {
      height: 100%; background: var(--accent);
      transition: width 0.4s ease; width: 0%;
    }

    #main-content { padding: 80px 24px 24px; }

    #dag-panel { margin-bottom: 24px; }
    .dag-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }
    .task-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 14px; transition: all 0.3s;
    }
    .state-running  { border-color: var(--accent); box-shadow: 0 0 12px rgba(26,115,232,0.3); }
    .state-complete { border-color: var(--success); }
    .state-failed   { border-color: var(--error); }

    .task-id    { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
    .task-type  { display: inline-block; font-size: 10px; font-weight: 700;
                   padding: 2px 8px; border-radius: 4px; margin-bottom: 8px; }
    .type-web_research     { background: #0F3460; color: #64B5F6; }
    .type-code_execution   { background: #1A3B1A; color: #81C784; }
    .type-data_processing  { background: #3B2A0F; color: #FFB74D; }
    .type-reasoning        { background: #2A0F3B; color: #CE93D8; }
    .type-document_generation { background: #0F3B2A; color: #80CBC4; }

    .task-description { font-size: 13px; line-height: 1.4; margin-bottom: 8px; }
    .task-state       { font-size: 11px; color: var(--muted); font-style: italic; }

    #result-panel { margin-top: 24px; }
    .result-item {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px; margin-bottom: 12px;
    }
    .result-item h3 { font-size: 14px; margin-bottom: 8px; color: var(--accent); }
    .result-item pre {
      font-size: 12px; color: #A8D8A8; background: #1E1E2E;
      padding: 12px; border-radius: 6px; overflow-x: auto;
      max-height: 300px; overflow-y: auto;
    }
  </style>
</head>
<body>
  <div id="intent-container">
    <input id="intent-input" type="text"
           placeholder="Describe what you want to accomplish...">
    <button id="intent-submit">Execute</button>
    <button id="intent-cancel">Cancel</button>
    <div id="intent-status" class="status status-info">Connecting...</div>
  </div>

  <div id="progress-container">
    <div id="progress-bar"></div>
  </div>

  <div id="main-content">
    <div id="dag-panel"></div>
    <div id="result-panel"></div>
  </div>

  <script src="intent_bar.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    # Save TypeScript and HTML files for browser development
    Path = __import__("pathlib").Path
    out_dir = Path("browser/src/intent_bar")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "intent_bar.ts").write_text(INTENT_BAR_TYPESCRIPT)
    (out_dir / "intent_bar.html").write_text(INTENT_BAR_HTML)
    print("Browser files written to browser/src/intent_bar/")

    # Start native messaging host
    host = NativeMessagingHost()
    asyncio.run(host.run())
