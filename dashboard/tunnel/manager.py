"""
CloudflareTunnelManager — optionally exposes the dashboard via a public URL.

Uses `cloudflared tunnel --url http://localhost:<port>` to create a temporary
public HTTPS URL. The URL is printed to the console and stored in
data/tunnel_url.txt so the agent can reference it.

cloudflared must be installed and in PATH (or set CLOUDFLARED_PATH env var).
If not installed, the manager silently disables itself.

Usage:
  manager = CloudflareTunnelManager(port=8080)
  await manager.start()          # starts cloudflared subprocess
  print(manager.public_url)      # e.g. https://abc123.trycloudflare.com
  await manager.stop()           # kills subprocess
"""

import asyncio
import logging
import os
import re
import subprocess
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_URL_DISCOVERY_TIMEOUT = 30  # seconds


class TunnelStatus(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    FAILED = auto()


class CloudflareTunnelManager:
    """Manages a Cloudflare Tunnel to expose the dashboard publicly.

    Two modes:
    - Temporary (default): cloudflared tunnel --url ... → random trycloudflare.com URL
    - Named tunnel (token mode): cloudflared tunnel run --token <token> → fixed URL
      Set tunnel_token + tunnel_hostname in config.yaml for a fixed persistent URL.

    cloudflared must be installed and in PATH (or set CLOUDFLARED_PATH env var).
    """

    def __init__(
        self,
        port: int = 8080,
        data_dir: str = "./data",
        cloudflared_path: Optional[str] = None,
        tunnel_token: Optional[str] = None,
        tunnel_hostname: Optional[str] = None,
    ) -> None:
        self._port = port
        self._data_dir = Path(data_dir)
        self._cloudflared_path: str = (
            cloudflared_path
            or os.environ.get("CLOUDFLARED_PATH", "cloudflared")
        )
        self._tunnel_token = tunnel_token or ""
        self._tunnel_hostname = tunnel_hostname or ""
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._public_url: Optional[str] = None
        self._status = TunnelStatus.STOPPED
        self._url_file = self._data_dir / "tunnel_url.txt"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def public_url(self) -> Optional[str]:
        """The public trycloudflare.com URL, or None if not running."""
        return self._public_url

    @property
    def status(self) -> TunnelStatus:
        """Current tunnel status."""
        return self._status

    async def start(self) -> bool:
        """Start the cloudflared tunnel.

        Returns True if the tunnel started and a public URL was discovered.
        Returns False if cloudflared is not available or the URL was not found
        within the discovery timeout.
        """
        if self._status == TunnelStatus.RUNNING:
            logger.warning("Tunnel is already running at %s", self._public_url)
            return True

        if not self.is_available():
            logger.warning(
                "cloudflared not found at %r — tunnel disabled", self._cloudflared_path
            )
            self._status = TunnelStatus.FAILED
            return False

        self._status = TunnelStatus.STARTING
        local_url = f"http://localhost:{self._port}"

        # Token mode: named persistent tunnel with a fixed hostname
        if self._tunnel_token:
            cmd = [self._cloudflared_path, "tunnel", "run", "--token", self._tunnel_token]
            logger.info("Starting named cloudflared tunnel (token mode)")
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except OSError as exc:
                logger.error("Failed to spawn cloudflared: %s", exc)
                self._status = TunnelStatus.FAILED
                return False
            # Give it a moment to connect, then mark running
            await asyncio.sleep(3)
            if self._proc.returncode is not None:
                logger.error("cloudflared exited immediately (bad token?)")
                self._status = TunnelStatus.FAILED
                return False
            url = self._tunnel_hostname or "(see Cloudflare dashboard)"
            self._public_url = url
            self._status = TunnelStatus.RUNNING
            self._write_url_file(url)
            asyncio.create_task(self._drain_stdout())
            logger.info("Cloudflare named tunnel running → %s", url)
            print(f"[Voyager] Dashboard public URL: {url}")
            return True

        # Temporary mode: random trycloudflare.com URL
        cmd = [self._cloudflared_path, "tunnel", "--url", local_url]
        logger.info("Starting cloudflared: %s", " ".join(cmd))
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
            )
        except OSError as exc:
            logger.error("Failed to spawn cloudflared: %s", exc)
            self._status = TunnelStatus.FAILED
            return False

        # Read stdout looking for the public URL within the timeout.
        try:
            url = await asyncio.wait_for(
                self._read_until_url(), timeout=_URL_DISCOVERY_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(
                "cloudflared did not produce a public URL within %ds — marking FAILED",
                _URL_DISCOVERY_TIMEOUT,
            )
            self._status = TunnelStatus.FAILED
            await self.stop()
            return False

        if url is None:
            logger.error("cloudflared exited before providing a public URL")
            self._status = TunnelStatus.FAILED
            return False

        self._public_url = url
        self._status = TunnelStatus.RUNNING
        self._write_url_file(url)
        logger.info("Cloudflare tunnel running: %s -> %s", url, local_url)
        print(f"[Voyager] Dashboard public URL: {url}")
        return True

    async def stop(self) -> None:
        """Stop the cloudflared subprocess and clear the URL file."""
        if self._proc is not None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except (ProcessLookupError, OSError):
                pass  # Already dead.
            self._proc = None

        self._public_url = None
        self._status = TunnelStatus.STOPPED
        self._clear_url_file()
        logger.info("Cloudflare tunnel stopped")

    @classmethod
    def is_available(cls) -> bool:
        """Return True if cloudflared is reachable via PATH (or CLOUDFLARED_PATH)."""
        binary = os.environ.get("CLOUDFLARED_PATH", "cloudflared")
        try:
            result = subprocess.run(
                [binary, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_url(self, output: str) -> Optional[str]:
        """Return the first trycloudflare.com URL found in output, or None."""
        match = _URL_PATTERN.search(output)
        return match.group(0) if match else None

    async def _read_until_url(self) -> Optional[str]:
        """Read lines from the subprocess stdout until a public URL is found.

        Returns the URL string, or None if the process exits without one.
        Continues reading in the background after returning so the process
        does not block on a full pipe buffer.
        """
        assert self._proc is not None
        assert self._proc.stdout is not None

        while True:
            line_bytes = await self._proc.stdout.readline()
            if not line_bytes:
                # EOF — process exited.
                return None
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            logger.debug("cloudflared: %s", line)
            url = self._find_url(line)
            if url:
                # Kick off a background task to keep draining stdout so the
                # subprocess doesn't stall on a full pipe.
                asyncio.create_task(self._drain_stdout())
                return url

    async def _drain_stdout(self) -> None:
        """Drain subprocess stdout lines to the debug log indefinitely."""
        if self._proc is None or self._proc.stdout is None:
            return
        try:
            async for line_bytes in self._proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                logger.debug("cloudflared: %s", line)
        except Exception:
            pass

    def _write_url_file(self, url: str) -> None:
        """Write the public URL to data/tunnel_url.txt."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._url_file.write_text(url, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write tunnel URL file: %s", exc)

    def _clear_url_file(self) -> None:
        """Remove data/tunnel_url.txt if it exists."""
        try:
            self._url_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove tunnel URL file: %s", exc)
