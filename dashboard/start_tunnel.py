"""
Standalone tunnel starter — starts ngrok and writes the public URL to
agent/data/tunnel_url.txt so the dashboard UI can display it.

Reads ngrok_domain from agent/config.yaml if set (for a fixed bookmarkable URL).
Otherwise uses a random temporary URL.

Run this in a separate process before starting uvicorn.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

DATA_DIR = Path(__file__).parent.parent / "agent" / "data"
NGROK_PATH = Path(__file__).parent.parent / "ngrok.exe"
NGROK_API = "http://localhost:4040/api/tunnels"


def _load_config() -> dict:
    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "agent" / "config.yaml"
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _get_tunnel_url(timeout: float = 30.0) -> str | None:
    """Poll ngrok's local API until a tunnel URL appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = json.loads(urlopen(NGROK_API, timeout=2).read())
            for t in data.get("tunnels", []):
                url = t.get("public_url", "")
                if url.startswith("https://"):
                    return url
        except (URLError, Exception):
            pass
        time.sleep(1)
    return None


def main() -> None:
    cfg = _load_config()
    dash = cfg.get("dashboard", {})
    port = dash.get("port", 7770)
    domain = dash.get("ngrok_domain", "") or ""

    ngrok = str(NGROK_PATH) if NGROK_PATH.exists() else "ngrok"

    cmd = [ngrok, "http", str(port), "--pooling-enabled"]
    if domain:
        cmd += ["--domain", domain]

    print(f"[Tunnel] Starting ngrok -> http://localhost:{port}", flush=True)
    if domain:
        print(f"[Tunnel] Using fixed domain: {domain}", flush=True)

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    url = _get_tunnel_url(timeout=30)
    if not url:
        print("[Tunnel] Could not get URL from ngrok — check ngrok is installed and authed.", flush=True)
        proc.terminate()
        return

    print(f"[Tunnel] Public URL: {url}", flush=True)
    print("[Tunnel] Running... close this window to stop.", flush=True)

    # Write URL for dashboard to display
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "tunnel_url.txt").write_text(url, encoding="utf-8")

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    finally:
        try:
            (DATA_DIR / "tunnel_url.txt").unlink(missing_ok=True)
        except Exception:
            pass
        print("[Tunnel] Stopped.", flush=True)


if __name__ == "__main__":
    main()
