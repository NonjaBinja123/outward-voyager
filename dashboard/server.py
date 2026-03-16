"""
Outward Voyager — Observation Dashboard

Lightweight FastAPI server that reads agent data files and serves
a real-time observation dashboard. No agent modification needed —
reads the same JSON/JSONL files the agent writes.

Run: uvicorn server:app --reload --port 8080
Then open: http://localhost:8080
"""
import asyncio
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Outward Voyager Dashboard")

DATA_DIR = Path(__file__).parent.parent / "agent" / "data"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_jsonl(path: Path, limit: int = 50) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return lines[-limit:]


# ── API endpoints ────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status() -> JSONResponse:
    """Quick health check — returns data file freshness."""
    files = {
        "novelty": DATA_DIR / "novelty.json",
        "preferences": DATA_DIR / "preferences.json",
        "mental_map": DATA_DIR / "mental_map.json",
        "combat_log": DATA_DIR / "combat_log.json",
        "self_modifications": DATA_DIR / "self_modifications.jsonl",
    }
    status = {}
    for name, path in files.items():
        if path.exists():
            age_s = time.time() - path.stat().st_mtime
            status[name] = {"exists": True, "age_seconds": round(age_s, 1)}
        else:
            status[name] = {"exists": False}
    return JSONResponse(status)


@app.get("/api/preferences")
def get_preferences() -> JSONResponse:
    data = _read_json(DATA_DIR / "preferences.json", {})
    prefs = list(data.values())
    prefs.sort(key=lambda p: abs(p.get("affinity", 0)), reverse=True)
    return JSONResponse(prefs[:30])


@app.get("/api/novelty")
def get_novelty() -> JSONResponse:
    data = _read_json(DATA_DIR / "novelty.json", {})
    items = [{"key": k, **v} for k, v in data.items()]
    items.sort(key=lambda x: x.get("encounter_count", 0), reverse=True)
    return JSONResponse(items[:50])


@app.get("/api/mental_map")
def get_mental_map() -> JSONResponse:
    # MentalMap saves the locations dict directly (no "locations" wrapper)
    data = _read_json(DATA_DIR / "mental_map.json", {})
    locs = list(data.values()) if isinstance(data, dict) else []
    locs.sort(key=lambda l: l.get("visit_count", 0), reverse=True)
    return JSONResponse(locs)


@app.get("/api/combat")
def get_combat() -> JSONResponse:
    data = _read_json(DATA_DIR / "combat_log.json", {"profiles": {}, "recent_records": []})
    profiles = list(data.get("profiles", {}).values())
    profiles.sort(
        key=lambda p: p.get("total_hp_loss_pct", 0) / max(1, p.get("encounter_count", 1)),
        reverse=True,
    )
    return JSONResponse({
        "profiles": profiles[:20],
        "recent": data.get("recent_records", [])[-10:],
    })


@app.get("/api/self_modifications")
def get_self_modifications() -> JSONResponse:
    records = _read_jsonl(DATA_DIR / "self_modifications.jsonl", limit=30)
    records.reverse()  # newest first
    return JSONResponse(records)


@app.get("/api/goals")
def get_goals() -> JSONResponse:
    session = _read_json(DATA_DIR / "session_goals.json", [])
    long_term = _read_json(DATA_DIR / "long_term_goals.json", [])
    return JSONResponse({"session": session, "long_term": long_term})


BEPINEX_LOG = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Outward\Outward_Defed\BepInEx\LogOutput.log")


@app.get("/api/log")
def get_log() -> JSONResponse:
    if not BEPINEX_LOG.exists():
        return JSONResponse([])
    try:
        lines = BEPINEX_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return JSONResponse(lines[-200:])  # last 200 lines
    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/api/chat")
def get_chat() -> JSONResponse:
    entries = _read_jsonl(DATA_DIR / "chat_log.jsonl", limit=100)
    return JSONResponse(entries)


class ChatMessage(BaseModel):
    message: str


@app.post("/api/chat")
def post_chat(body: ChatMessage) -> JSONResponse:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="empty message")
    pending_path = DATA_DIR / "pending_dashboard_chat.json"
    try:
        existing: list = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else []
    except Exception:
        existing = []
    existing.append({"timestamp": time.time(), "message": message})
    pending_path.write_text(json.dumps(existing), encoding="utf-8")
    return JSONResponse({"ok": True})


@app.get("/api/game_state")
def get_game_state() -> JSONResponse:
    """Current game state — written by orchestrator every state push."""
    data = _read_json(DATA_DIR / "game_state.json", {})
    return JSONResponse(data)


@app.get("/api/llm_usage")
def get_llm_usage() -> JSONResponse:
    """LLM provider usage stats."""
    data = _read_json(DATA_DIR / "llm_usage.json", {})
    return JSONResponse(data)


@app.get("/api/skills")
def get_skills() -> JSONResponse:
    """Read skills SQLite DB directly."""
    import sqlite3
    db_path = DATA_DIR / "skills.db"
    if not db_path.exists():
        return JSONResponse([])
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT name, action_type, tags, success_rate, times_used, description "
            "FROM skills ORDER BY success_rate DESC"
        ).fetchall()
        conn.close()
        return JSONResponse([
            {"name": r[0], "action_type": r[1], "tags": json.loads(r[2]),
             "success_rate": r[3], "times_used": r[4], "description": r[5]}
            for r in rows
        ])
    except Exception as e:
        return JSONResponse({"error": str(e)})


# ── Game stream ──────────────────────────────────────────────────────────────
# Dedicated background thread captures frames; WebSocket pushes them to browser.

_latest_frame: bytes | None = None  # GIL makes byte reference assignment atomic
_capture_error: str = ""


def _capture_loop() -> None:
    global _latest_frame, _capture_error
    try:
        from windows_capture import WindowsCapture, Frame, InternalCaptureControl
        from PIL import Image
    except ImportError as e:
        _capture_error = f"Import failed: {e}"
        return

    while True:
        try:
            done = threading.Event()
            capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                window_name="Outward: Definitive Edition",
            )

            @capture.event
            def on_frame_arrived(frame: Frame, _: InternalCaptureControl) -> None:
                global _latest_frame
                try:
                    bgra = frame.frame_buffer
                    rgb = bgra[:, :, [2, 1, 0]]
                    buf = io.BytesIO()
                    from PIL import Image as _Image
                    img = _Image.fromarray(rgb, "RGB")
                    img.thumbnail((960, 540))
                    img.save(buf, format="JPEG", quality=70)
                    _latest_frame = buf.getvalue()  # atomic ref assignment
                except Exception as e:
                    global _capture_error
                    _capture_error = f"Frame error: {e}"

            @capture.event
            def on_closed() -> None:
                done.set()

            _capture_error = "capturing"
            capture.start_free_threaded()
            done.wait()
            _capture_error = "window closed, retrying"
        except Exception as e:
            _capture_error = f"Capture failed: {e}"
        time.sleep(3)


threading.Thread(target=_capture_loop, daemon=True, name="FrameCapture").start()


@app.get("/api/stream_status")
def stream_status() -> JSONResponse:
    return JSONResponse({
        "has_frame": _latest_frame is not None,
        "frame_size": len(_latest_frame) if _latest_frame else 0,
        "status": _capture_error,
    })


@app.websocket("/ws/stream")
async def stream_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            frame = _latest_frame
            if frame:
                await ws.send_bytes(frame)
            await asyncio.sleep(1 / 15)
    except (WebSocketDisconnect, Exception):
        pass


# ── Dashboard HTML ───────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Outward Voyager Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0f0f1a; color: #c9d1d9; }
header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px;
         display: flex; align-items: center; gap: 16px; }
header h1 { font-size: 1.2rem; color: #e6edf3; }
.badge { background: #238636; color: #fff; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
.badge.offline { background: #b91c1c; }
main { position: relative; min-height: 1400px; overflow-x: auto; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; position: absolute; resize: both; overflow: auto; min-width: 260px; min-height: 80px; box-sizing: border-box; }
.card > h2 { font-size: 0.9rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; cursor: grab; user-select: none; }
.card > h2:active { cursor: grabbing; }
.bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 0.82rem; }
.bar-label { width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { flex: 1; background: #21262d; border-radius: 3px; height: 8px; }
.bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
.bar-fill.pos { background: #238636; }
.bar-fill.neg { background: #b91c1c; }
.bar-fill.neu { background: #1f6feb; }
.val { width: 50px; text-align: right; color: #8b949e; }
table { width: 100%; font-size: 0.8rem; border-collapse: collapse; }
th { text-align: left; color: #8b949e; border-bottom: 1px solid #30363d; padding: 4px 8px; }
td { padding: 4px 8px; border-bottom: 1px solid #21262d; }
.tag { background: #1f6feb22; color: #58a6ff; border-radius: 4px; padding: 1px 5px;
       font-size: 0.72rem; margin-right: 2px; }
.mono { font-family: monospace; font-size: 0.78rem; }
.refresh-time { font-size: 0.72rem; color: #444; margin-top: 8px; }
</style>
</head>
<body>
<header>
  <h1>Outward Voyager</h1>
  <span class="badge" id="status-badge">connecting...</span>
  <span style="font-size:0.72rem;color:#555;background:#0d1117;padding:2px 8px;border-radius:4px;font-family:monospace">build: 2026-03-15 v9</span>
  <input id="player-name" placeholder="Your name" title="Your display name in chat"
    style="background:#21262d;border:1px solid #30363d;border-radius:4px;padding:3px 8px;color:#e6edf3;font-size:0.8rem;width:120px"
    oninput="localStorage.setItem('voy_name',this.value)">
  <button onclick="localStorage.removeItem('voy_layout');location.reload()"
    style="background:#21262d;border:1px solid #30363d;border-radius:4px;padding:3px 10px;color:#8b949e;cursor:pointer;font-size:0.8rem">Reset Layout</button>
  <span style="margin-left:auto;font-size:0.8rem;color:#8b949e" id="last-refresh"></span>
</header>
<main>
  <div class="card" id="card-chat">
    <h2>Chat with Voyager</h2>
    <div id="chat-log" style="height:220px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;margin-bottom:10px;padding-right:4px"></div>
    <div style="display:flex;gap:8px">
      <input id="chat-input" type="text" placeholder="Say something to Voyager..."
        style="flex:1;background:#21262d;border:1px solid #30363d;border-radius:6px;padding:8px 12px;color:#e6edf3;font-size:0.9rem;outline:none"
        onkeydown="if(event.key==='Enter')sendChat()">
      <button onclick="sendChat()"
        style="background:#238636;border:none;border-radius:6px;padding:8px 16px;color:#fff;cursor:pointer;font-size:0.9rem">Send</button>
    </div>
  </div>
  <div class="card" id="card-diag" style="background:#1a0f0f;border-color:#5a1e1e">
    <h2 style="color:#f85149">Diagnostics</h2>
    <div id="diag-body" style="font-family:monospace;font-size:0.78rem;display:flex;flex-direction:column;gap:4px">
      <div>Stream capture: <span id="diag-capture" style="color:#8b949e">checking...</span></div>
      <div>WS connection: <span id="diag-ws" style="color:#8b949e">not connected</span></div>
      <div>Frames received: <span id="diag-frames" style="color:#8b949e">0</span></div>
      <div>Log file: <span id="diag-log" style="color:#8b949e">checking...</span></div>
      <div>Last log lines: <span id="diag-logcount" style="color:#8b949e">-</span></div>
      <div>Last API error: <span id="diag-error" style="color:#f85149">none</span></div>
    </div>
  </div>
  <div class="card" id="card-stream">
    <h2>Live Game Feed <span id="stream-status" style="font-size:0.75rem;color:#8b949e;font-weight:normal;text-transform:none;letter-spacing:0"></span></h2>
    <img id="game-stream" alt="Game feed"
      style="width:100%;border-radius:6px;display:block;background:#000;min-height:200px">
  </div>
  <div class="card" id="card-log">
    <h2>BepInEx Console</h2>
    <div id="log-body" style="height:200px;overflow-y:auto;font-family:monospace;font-size:0.75rem;background:#0d1117;border-radius:6px;padding:8px;white-space:pre-wrap;word-break:break-all"><span style="color:#444">Waiting for log...</span></div>
  </div>
  <div class="card" id="card-state">
    <h2>Game State</h2>
    <div id="state-body" style="font-family:monospace;font-size:0.82rem;display:flex;flex-direction:column;gap:4px">
      <span style="color:#8b949e">Waiting for game state...</span>
    </div>
  </div>
  <div class="card" id="card-llm">
    <h2>LLM Usage</h2>
    <div id="llm-body" style="font-size:0.82rem">
      <span style="color:#8b949e">No usage data yet</span>
    </div>
  </div>
  <div class="card" id="card-prefs">
    <h2>Preferences</h2>
    <div id="prefs-body">Loading...</div>
  </div>
  <div class="card" id="card-combat">
    <h2>Combat Knowledge</h2>
    <div id="combat-body">Loading...</div>
  </div>
  <div class="card" id="card-map">
    <h2>Mental Map</h2>
    <div id="map-body">Loading...</div>
  </div>
  <div class="card" id="card-skills">
    <h2>Skill Library</h2>
    <div id="skills-body">Loading...</div>
  </div>
  <div class="card" id="card-sandbox">
    <h2>Self-Modifications</h2>
    <div id="sandbox-body">Loading...</div>
  </div>
  <div class="card" id="card-goals">
    <h2>Active Goals</h2>
    <div id="goals-body">Loading...</div>
  </div>
  <div class="card" id="card-novelty">
    <h2>Novelty (top discoveries)</h2>
    <div id="novelty-body">Loading...</div>
  </div>
</main>
<script>
// Block 1: error catcher — runs before the main script so syntax errors are visible
window.onerror = function(msg, src, line, col, err) {
  var el = document.getElementById('diag-error');
  if (el) el.textContent = 'JS error line ' + line + ': ' + msg;
  var card = document.getElementById('card-diag');
  if (card) card.style.borderColor = '#f85149';
  return false;
};
document.getElementById('diag-ws').textContent = 'script-1-ok';
</script>
<script>
// Block 2: main dashboard code
async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    return r.json();
  } catch(e) {
    return null;
  }
}

function bar(label, value, min=-1, max=1, colorClass='neu') {
  const pct = ((value - min) / (max - min)) * 100;
  const cls = value > 0.05 ? 'pos' : value < -0.05 ? 'neg' : 'neu';
  return `<div class="bar-row">
    <span class="bar-label" title="${label}">${label}</span>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${Math.max(0,Math.min(100,pct))}%"></div></div>
    <span class="val">${value >= 0 ? '+' : ''}${value.toFixed(2)}</span>
  </div>`;
}

async function refreshPrefs() {
  const data = await fetchJSON('/api/preferences');
  const html = data.slice(0, 12).map(p =>
    bar(`${p.category}:${p.name}`, p.affinity)
  ).join('') || '<span style="color:#8b949e">No preferences yet</span>';
  document.getElementById('prefs-body').innerHTML = html;
}

async function refreshCombat() {
  const data = await fetchJSON('/api/combat');
  const profiles = data.profiles || [];
  if (!profiles.length) {
    document.getElementById('combat-body').innerHTML = '<span style="color:#8b949e">No combat data yet</span>';
    return;
  }
  const rows = profiles.slice(0, 8).map(p => {
    const sr = (p.survival_count / Math.max(1, p.encounter_count) * 100).toFixed(0);
    const avgLoss = (p.total_hp_loss_pct / Math.max(1, p.encounter_count) * 100).toFixed(1);
    return `<tr><td>${p.name}</td><td>${p.encounter_count}</td><td>${sr}%</td><td>${avgLoss}%</td></tr>`;
  }).join('');
  document.getElementById('combat-body').innerHTML = `
    <table><thead><tr><th>Enemy</th><th>Enc.</th><th>Survived</th><th>Avg HP lost</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function refreshMap() {
  const data = await fetchJSON('/api/mental_map');
  if (!data.length) {
    document.getElementById('map-body').innerHTML = '<span style="color:#8b949e">No locations visited</span>';
    return;
  }
  const rows = data.slice(0, 10).map(l =>
    `<tr><td>${l.scene}</td><td>${l.visit_count || 0}</td><td>${l.familiarity ? l.familiarity.toFixed(2) : '?'}</td></tr>`
  ).join('');
  document.getElementById('map-body').innerHTML = `
    <table><thead><tr><th>Scene</th><th>Visits</th><th>Familiarity</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function refreshSkills() {
  const data = await fetchJSON('/api/skills');
  if (!data.length) {
    document.getElementById('skills-body').innerHTML = '<span style="color:#8b949e">No skills yet</span>';
    return;
  }
  const rows = data.slice(0, 10).map(s => {
    const tags = (s.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
    return `<tr><td>${s.name}</td><td>${s.action_type}</td><td>${(s.success_rate*100).toFixed(0)}%</td><td>${tags}</td></tr>`;
  }).join('');
  document.getElementById('skills-body').innerHTML = `
    <table><thead><tr><th>Name</th><th>Type</th><th>Success</th><th>Tags</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function refreshSandbox() {
  const data = await fetchJSON('/api/self_modifications');
  if (!data.length) {
    document.getElementById('sandbox-body').innerHTML = '<span style="color:#8b949e">No self-modifications yet</span>';
    return;
  }
  const rows = data.slice(0, 8).map(r => {
    const ts = new Date(r.timestamp * 1000).toLocaleTimeString();
    const ok = r.integrated ? '✓' : '✗';
    const color = r.integrated ? '#238636' : '#b91c1c';
    return `<tr><td><span style="color:${color}">${ok}</span></td><td>${r.name}</td><td>${r.stage}</td><td class="mono">${ts}</td></tr>`;
  }).join('');
  document.getElementById('sandbox-body').innerHTML = `
    <table><thead><tr><th></th><th>Skill</th><th>Stage</th><th>Time</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function refreshNovelty() {
  const data = await fetchJSON('/api/novelty');
  const rows = data.slice(0, 10).map(n =>
    `<tr><td>${n.key}</td><td>${n.encounter_count}</td></tr>`
  ).join('');
  document.getElementById('novelty-body').innerHTML = rows
    ? `<table><thead><tr><th>Key</th><th>Encounters</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<span style="color:#8b949e">No data yet</span>';
}

function facingDir(y) {
  y = ((y % 360) + 360) % 360;
  if (y < 22.5 || y >= 337.5) return '(N)';
  if (y < 67.5) return '(NE)';
  if (y < 112.5) return '(E)';
  if (y < 157.5) return '(SE)';
  if (y < 202.5) return '(S)';
  if (y < 247.5) return '(SW)';
  if (y < 292.5) return '(W)';
  return '(NW)';
}

async function refreshGameState() {
  const data = await fetchJSON('/api/game_state');
  const el = document.getElementById('state-body');
  if (!data || !data.player) {
    el.innerHTML = '<span style="color:#e3b341">No game state — game not loaded or agent not connected</span>';
    return;
  }
  const p = data.player;
  const hp = p.health || 0, maxHp = p.max_health || 100;
  const stam = p.stamina || 0, maxStam = p.max_stamina || 100;
  const mana = p.mana || 0, maxMana = p.max_mana || 0;
  const hpPct = (hp / Math.max(1, maxHp) * 100).toFixed(0);
  const stamPct = (stam / Math.max(1, maxStam) * 100).toFixed(0);
  const hpColor = hpPct > 50 ? '#3fb950' : hpPct > 25 ? '#e3b341' : '#f85149';
  const stamColor = stamPct > 50 ? '#3fb950' : stamPct > 25 ? '#e3b341' : '#f85149';
  el.innerHTML = `
    <div>Scene: <span style="color:#58a6ff">${data.scene || 'unknown'}</span></div>
    <div>Health: <span style="color:${hpColor}">${hp.toFixed(0)}/${maxHp.toFixed(0)} (${hpPct}%)</span></div>
    <div>Stamina: <span style="color:${stamColor}">${stam.toFixed(0)}/${maxStam.toFixed(0)} (${stamPct}%)</span></div>
    ${maxMana > 0 ? `<div>Mana: <span style="color:#bc8cff">${mana.toFixed(0)}/${maxMana.toFixed(0)}</span></div>` : ''}
    <div>Position: <span style="color:#8b949e">(${(p.pos_x||0).toFixed(1)}, ${(p.pos_y||0).toFixed(1)}, ${(p.pos_z||0).toFixed(1)})</span></div>
    <div>Facing: <span style="color:#8b949e">${((p.rotation_y||0) % 360).toFixed(0)}° ${facingDir(p.rotation_y||0)}</span></div>
    <div>Combat: <span style="color:${p.in_combat ? '#f85149' : '#3fb950'}">${p.in_combat ? 'YES' : 'no'}</span>
         Dead: <span style="color:${p.is_dead ? '#f85149' : '#3fb950'}">${p.is_dead ? 'YES' : 'no'}</span></div>`;
}

async function refreshLLMUsage() {
  const data = await fetchJSON('/api/llm_usage');
  const el = document.getElementById('llm-body');
  if (!data || !Object.keys(data).length) {
    el.innerHTML = '<span style="color:#8b949e">No usage data yet</span>';
    return;
  }
  const rows = Object.entries(data).map(([name, info]) => {
    const cost = info.est_cost_usd > 0 ? `$${info.est_cost_usd.toFixed(4)}` : 'free';
    return `<tr>
      <td style="color:#58a6ff">${name}</td>
      <td>${info.calls}</td>
      <td style="color:${info.failures > 0 ? '#f85149' : '#3fb950'}">${info.failures}</td>
      <td>${cost}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table><thead><tr><th>Provider</th><th>Calls</th><th>Fails</th><th>Est. Cost</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function checkStatus() {
  try {
    const data = await fetchJSON('/api/status');
    const anyFresh = Object.values(data).some(v => v.exists && v.age_seconds < 60);
    const badge = document.getElementById('status-badge');
    badge.textContent = anyFresh ? 'live' : 'stale';
    badge.className = 'badge' + (anyFresh ? '' : ' offline');
  } catch {
    document.getElementById('status-badge').textContent = 'offline';
    document.getElementById('status-badge').className = 'badge offline';
  }
}

async function refreshGoals() {
  const data = await fetchJSON('/api/goals');
  const session = (data.session || []).filter(g => !g.completed);
  const longTerm = (data.long_term || []).filter(g => !g.completed);
  const allGoals = [...session.map(g => ({...g, _type: 'session'})),
                    ...longTerm.map(g => ({...g, _type: 'long-term'}))];
  if (!allGoals.length) {
    document.getElementById('goals-body').innerHTML = '<span style="color:#8b949e">No active goals</span>';
    return;
  }
  allGoals.sort((a, b) => (b.priority || 5) - (a.priority || 5));
  const rows = allGoals.map(g =>
    `<tr><td>${g.priority || 5}</td><td>${g.description}</td><td><span class="tag">${g._type}</span></td></tr>`
  ).join('');
  document.getElementById('goals-body').innerHTML = `
    <table><thead><tr><th>Pri</th><th>Goal</th><th>Type</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ── Diagnostics ──────────────────────────────────────────────────────────
let _diagFrameCount = 0;
async function refreshDiag() {
  try {
    const r = await fetch('/api/stream_status');
    const d = await r.json();
    const el = document.getElementById('diag-capture');
    el.textContent = `${d.status} | has_frame=${d.has_frame} | size=${d.frame_size}b`;
    el.style.color = d.has_frame ? '#3fb950' : '#e3b341';
  } catch(e) {
    document.getElementById('diag-capture').textContent = 'API call failed: ' + e;
    document.getElementById('diag-capture').style.color = '#f85149';
  }
  try {
    const r2 = await fetch('/api/log');
    const lines = await r2.json();
    const logEl = document.getElementById('diag-log');
    if (Array.isArray(lines)) {
      logEl.textContent = `exists, ${lines.length} lines`;
      logEl.style.color = lines.length > 0 ? '#3fb950' : '#e3b341';
      document.getElementById('diag-logcount').textContent = lines.length > 0 ? lines[lines.length-1].substring(0,80) : '(empty)';
    } else {
      logEl.textContent = 'error: ' + JSON.stringify(lines);
      logEl.style.color = '#f85149';
    }
  } catch(e) {
    document.getElementById('diag-log').textContent = 'API call failed: ' + e;
    document.getElementById('diag-log').style.color = '#f85149';
  }
}

// ── Game stream via WebSocket ─────────────────────────────────────────────
(function() {
  const img = document.getElementById('game-stream');
  const status = document.getElementById('stream-status');
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws/stream`);
    ws.binaryType = 'blob';
    status.textContent = 'connecting...';
    document.getElementById('diag-ws').textContent = 'connecting...';
    ws.onopen = () => {
      status.textContent = 'live';
      status.style.color = '#3fb950';
      document.getElementById('diag-ws').textContent = 'open';
      document.getElementById('diag-ws').style.color = '#3fb950';
    };
    ws.onmessage = e => {
      _diagFrameCount++;
      document.getElementById('diag-frames').textContent = _diagFrameCount + ' (size=' + e.data.size + 'b)';
      document.getElementById('diag-frames').style.color = '#3fb950';
      const reader = new FileReader();
      reader.onload = () => {
        img.src = reader.result;
      };
      reader.onerror = err => {
        document.getElementById('diag-error').textContent = 'FileReader error: ' + err;
      };
      reader.readAsDataURL(e.data);
    };
    ws.onerror = e => {
      document.getElementById('diag-ws').textContent = 'error';
      document.getElementById('diag-ws').style.color = '#f85149';
    };
    ws.onclose = () => {
      status.textContent = 'reconnecting...';
      status.style.color = '#8b949e';
      document.getElementById('diag-ws').textContent = 'closed, reconnecting...';
      setTimeout(connect, 2000);
    };
  }
  connect();
})();

let _lastChatCount = 0;
async function refreshChat() {
  const data = await fetchJSON('/api/chat');
  if (data.length === _lastChatCount) return;
  _lastChatCount = data.length;
  const log = document.getElementById('chat-log');
  log.innerHTML = data.map(entry => {
    const isVoyager = entry.role === 'voyager';
    const name = isVoyager ? 'Voyager' : (localStorage.getItem('voy_name') || entry.name || 'Player');
    const color = isVoyager ? '#58a6ff' : '#3fb950';
    const ts = new Date(entry.timestamp * 1000).toLocaleTimeString();
    return `<div style="display:flex;gap:8px;align-items:flex-start">
      <span style="color:${color};font-weight:600;white-space:nowrap;font-size:0.82rem">${name}</span>
      <span style="font-size:0.85rem;flex:1">${entry.message}</span>
      <span style="color:#444;font-size:0.72rem;white-space:nowrap">${ts}</span>
    </div>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  await fetch('/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message})
  });
  setTimeout(refreshChat, 500);
}

let _lastLogCount = 0;
async function refreshLog() {
  let lines;
  try {
    const r = await fetch('/api/log');
    lines = await r.json();
  } catch(e) {
    document.getElementById('log-body').innerHTML = `<span style="color:#f85149">fetch error: ${e}</span>`;
    return;
  }
  if (!Array.isArray(lines)) {
    document.getElementById('log-body').innerHTML = `<span style="color:#f85149">unexpected response: ${JSON.stringify(lines)}</span>`;
    return;
  }
  if (lines.length === _lastLogCount) return;
  _lastLogCount = lines.length;
  const el = document.getElementById('log-body');
  const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 40;
  if (lines.length === 0) {
    el.innerHTML = '<span style="color:#e3b341">Log file exists but is empty</span>';
    return;
  }
  el.innerHTML = lines.map(l => {
    let color = '#c9d1d9';
    if (l.includes('[Warning')) color = '#e3b341';
    else if (l.includes('[Error') || l.includes('[Fatal')) color = '#f85149';
    else if (l.includes('[Info') && l.includes('OutwardVoyager')) color = '#79c0ff';
    return `<span style="color:${color}">${l.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span>`;
  }).join('\\n');
  if (atBottom) el.scrollTop = el.scrollHeight;
}

async function safeRun(fn) { try { await fn(); } catch(e) {} }

// ── Draggable / resizable layout ─────────────────────────────────────────
var _DEFAULT_LAYOUT = {
  'card-chat':    {left:20,   top:20,   width:600, height:340},
  'card-state':   {left:640,  top:20,   width:300, height:220},
  'card-llm':     {left:960,  top:20,   width:300, height:220},
  'card-stream':  {left:640,  top:260,  width:600, height:380},
  'card-diag':    {left:1280, top:20,   width:340, height:260},
  'card-log':     {left:1280, top:300,  width:340, height:340},
  'card-prefs':   {left:20,   top:380,  width:440, height:280},
  'card-combat':  {left:480,  top:380,  width:440, height:280},
  'card-map':     {left:940,  top:660,  width:440, height:280},
  'card-skills':  {left:20,   top:680,  width:440, height:280},
  'card-sandbox': {left:480,  top:680,  width:440, height:280},
  'card-goals':   {left:940,  top:380,  width:440, height:280},
  'card-novelty': {left:20,   top:980,  width:440, height:280}
};

function _saveLayout() {
  var layout = {};
  document.querySelectorAll('.card').forEach(function(card) {
    layout[card.id] = {left:card.offsetLeft, top:card.offsetTop, width:card.offsetWidth, height:card.offsetHeight};
  });
  localStorage.setItem('voy_layout', JSON.stringify(layout));
}

function _applyLayout(layout) {
  var maxBottom = 0;
  document.querySelectorAll('.card').forEach(function(card) {
    var pos = layout[card.id];
    if (!pos) return;
    card.style.left = pos.left + 'px';
    card.style.top  = pos.top  + 'px';
    card.style.width  = pos.width  + 'px';
    card.style.height = pos.height + 'px';
    maxBottom = Math.max(maxBottom, pos.top + pos.height + 40);
  });
  document.querySelector('main').style.minHeight = maxBottom + 'px';
}

function initLayout() {
  document.querySelectorAll('.card').forEach(function(card) {
    var h2 = card.querySelector('h2');
    if (!h2) return;
    var dragging = false, sx, sy, ox, oy;
    h2.addEventListener('mousedown', function(e) {
      dragging = true; sx = e.clientX; sy = e.clientY;
      ox = card.offsetLeft; oy = card.offsetTop;
      e.preventDefault();
    });
    document.addEventListener('mousemove', function(e) {
      if (!dragging) return;
      card.style.left = (ox + e.clientX - sx) + 'px';
      card.style.top  = (oy + e.clientY - sy) + 'px';
    });
    document.addEventListener('mouseup', function() {
      if (dragging) { dragging = false; _saveLayout(); }
    });
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function() { _saveLayout(); }).observe(card);
    }
  });
  var saved = localStorage.getItem('voy_layout');
  _applyLayout(saved ? JSON.parse(saved) : _DEFAULT_LAYOUT);
}

async function refreshAll() {
  document.getElementById('last-refresh').textContent = 'Refreshing...';
  await Promise.all([
    refreshDiag(), refreshChat(), refreshLog(), refreshPrefs(), refreshCombat(), refreshMap(),
    refreshSkills(), refreshSandbox(), refreshNovelty(), refreshGoals(), checkStatus(),
    refreshGameState(), refreshLLMUsage()
  ].map(p => p.catch ? p.catch(e => { document.getElementById('diag-error').textContent = String(e); }) : p));
  document.getElementById('last-refresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();
}

(function() {
  var saved = localStorage.getItem('voy_name');
  if (saved) document.getElementById('player-name').value = saved;
})();
initLayout();
refreshAll();
setInterval(refreshAll, 10000);
setInterval(refreshLog, 2000);   // live log updates
setInterval(refreshChat, 3000);  // fast chat updates
setInterval(refreshGameState, 5000);  // game state every 5s
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_HTML, headers={"Cache-Control": "no-store"})
