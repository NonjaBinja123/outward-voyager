"""
Outward Voyager — Observation Dashboard

Lightweight FastAPI server that reads agent data files and serves
a real-time observation dashboard. No agent modification needed —
reads the same JSON/JSONL files the agent writes.

Run: uvicorn server:app --reload --port 8080
Then open: http://localhost:8080
"""
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

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
    data = _read_json(DATA_DIR / "mental_map.json", {"locations": {}})
    locs = list(data.get("locations", {}).values())
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
main { padding: 20px; display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.card h2 { font-size: 0.9rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }
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
  <span style="margin-left:auto;font-size:0.8rem;color:#8b949e" id="last-refresh"></span>
</header>
<main>
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
  <div class="card" id="card-novelty">
    <h2>Novelty (top discoveries)</h2>
    <div id="novelty-body">Loading...</div>
  </div>
</main>
<script>
async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
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

async function refreshAll() {
  document.getElementById('last-refresh').textContent = 'Refreshing...';
  await Promise.all([
    refreshPrefs(), refreshCombat(), refreshMap(),
    refreshSkills(), refreshSandbox(), refreshNovelty(), checkStatus()
  ]);
  document.getElementById('last-refresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();
}

refreshAll();
setInterval(refreshAll, 10000);  // refresh every 10s
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_HTML)
