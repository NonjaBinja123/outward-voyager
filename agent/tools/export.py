"""
voyager export — snapshot all agent state to a timestamped zip file.

Captures:
  - data/                  (JSON, JSONL, SQLite, ChromaDB)
  - agent/sandbox/skills/  (self-written code)
  - agent/config.yaml
  - logs/voyager.log (last 5000 lines)

Usage:
  py tools/export.py                   # exports to exports/voyager_<timestamp>.zip
  py tools/export.py --out ./backups   # custom output directory
"""
import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

# Resolve project root (two levels up from this file: agent/tools/ → agent/ → project/)
_HERE = Path(__file__).parent
_AGENT_DIR = _HERE.parent
_PROJECT_DIR = _AGENT_DIR.parent


def _add_dir(zf: zipfile.ZipFile, src: Path, arc_prefix: str,
             exts: set[str] | None = None,
             skip: set[str] | None = None) -> int:
    """Recursively add files from src to zip under arc_prefix. Returns count added."""
    if not src.exists():
        return 0
    count = 0
    for item in sorted(src.rglob("*")):
        if item.is_dir():
            continue
        if skip and item.name in skip:
            continue
        if exts and item.suffix.lower() not in exts:
            continue
        arc_name = arc_prefix + "/" + item.relative_to(src).as_posix()
        zf.write(item, arc_name)
        count += 1
    return count


def _add_log_tail(zf: zipfile.ZipFile, log_path: Path, arc_name: str,
                  max_lines: int = 5000) -> bool:
    """Add the last max_lines of a log file to the zip."""
    if not log_path.exists():
        return False
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-max_lines:])
        zf.writestr(arc_name, tail)
        return True
    except Exception as e:
        print(f"  [warn] Could not read {log_path}: {e}", file=sys.stderr)
        return False


def export(out_dir: Path | None = None) -> Path:
    """Run the export and return the path to the created zip file."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir or _PROJECT_DIR / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"voyager_{ts}.zip"

    data_dir = _AGENT_DIR / "data"
    sandbox_skills_dir = _AGENT_DIR / "sandbox" / "skills"
    config_file = _AGENT_DIR / "config.yaml"
    log_file = _AGENT_DIR / "logs" / "voyager.log"

    manifest: dict = {
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": {},
    }

    print(f"Exporting to {zip_path} ...")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # ── data/ directory ───────────────────────────────────────────────────
        n = _add_dir(
            zf, data_dir, "data",
            exts={".json", ".jsonl", ".db", ".sqlite"},
            skip={"__pycache__"},
        )
        print(f"  data/           {n} files")
        manifest["files"]["data"] = n

        # ── ChromaDB (binary/misc files inside data/chroma/) ──────────────────
        chroma_dir = data_dir / "chroma"
        if chroma_dir.exists():
            nc = _add_dir(zf, chroma_dir, "data/chroma")
            print(f"  data/chroma/    {nc} files (already included above if .sqlite)")

        # ── Sandbox skills (agent-written Python) ─────────────────────────────
        ns = _add_dir(
            zf, sandbox_skills_dir, "sandbox_skills",
            exts={".py"},
            skip={"__pycache__"},
        )
        print(f"  sandbox/skills/ {ns} files")
        manifest["files"]["sandbox_skills"] = ns

        # ── Config ────────────────────────────────────────────────────────────
        if config_file.exists():
            zf.write(config_file, "config.yaml")
            print(f"  config.yaml     OK")
            manifest["files"]["config"] = 1

        # ── Log tail ──────────────────────────────────────────────────────────
        ok = _add_log_tail(zf, log_file, "voyager.log.tail")
        print(f"  voyager.log     {'OK (last 5000 lines)' if ok else '(not found)'}")
        manifest["files"]["log"] = 1 if ok else 0

        # ── Manifest ──────────────────────────────────────────────────────────
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    size_kb = zip_path.stat().st_size // 1024
    print(f"\nExport complete: {zip_path} ({size_kb} KB)")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Outward Voyager agent state to a zip file.")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: project/exports/)")
    args = parser.parse_args()
    export(out_dir=args.out)


if __name__ == "__main__":
    # Run from any directory — resolve paths relative to this file
    sys.path.insert(0, str(_AGENT_DIR))
    main()
