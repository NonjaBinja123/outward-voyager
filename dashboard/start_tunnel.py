"""
Standalone tunnel starter — runs cloudflared and writes the public URL to
data/tunnel_url.txt so the dashboard UI can display it.

Run this in a separate process before (or alongside) starting uvicorn.
It keeps running until killed — killing it stops the tunnel.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tunnel.manager import CloudflareTunnelManager

DATA_DIR = Path(__file__).parent.parent / "agent" / "data"


async def main() -> None:
    import yaml
    cfg_path = Path(__file__).parent.parent / "agent" / "config.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    dash = cfg.get("dashboard", {})
    port = dash.get("port", 7770)
    token = dash.get("cloudflare_tunnel_token", "") or ""
    hostname = dash.get("tunnel_hostname", "") or ""

    mgr = CloudflareTunnelManager(
        port=port,
        data_dir=str(DATA_DIR),
        tunnel_token=token,
        tunnel_hostname=hostname,
    )
    ok = await mgr.start()
    if not ok:
        print("[Tunnel] Failed to start — is cloudflared installed?", flush=True)
        return

    print(f"[Tunnel] Public URL: {mgr.public_url}", flush=True)
    print("[Tunnel] Running... press Ctrl+C to stop.", flush=True)

    # Keep alive until killed
    try:
        while True:
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await mgr.stop()
        print("[Tunnel] Stopped.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
