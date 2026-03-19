"""
voyager migrate — transfer accumulated experience to a fresh agent instance.

Copies the "portable" parts of agent state (preferences, novelty, mental map,
relationships, social memory, journal, sandbox skills) into a target data directory.

Intentionally does NOT copy:
  - skills.db (game-specific skill commands)
  - goals (session/long-term goals are game-specific)
  - chat_log.jsonl (conversation history)
  - llm_usage.json (billing stats)

Usage:
  py tools/migrate.py --to ./new_data
  py tools/migrate.py --from ./old_data --to ./new_data
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent
_AGENT_DIR = _HERE.parent

# Files that carry over to a new game/agent instance
_PORTABLE_DATA_FILES = [
    "novelty.json",
    "preferences.json",
    "mental_map.json",
    "combat_log.json",
    "relationships.json",
    "social_memory.jsonl",
    "keybindings.json",
    "self_modifications.jsonl",
]

_PORTABLE_DIRS = [
    "chroma",   # ChromaDB journal — semantic memories persist
]


def migrate(src_data: Path, dst_data: Path, dry_run: bool = False) -> None:
    """Copy portable files from src_data to dst_data."""
    dst_data.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0

    print(f"Migrating from: {src_data}")
    print(f"Migrating to:   {dst_data}")
    if dry_run:
        print("(dry run — no files will be changed)\n")
    else:
        print()

    for filename in _PORTABLE_DATA_FILES:
        src = src_data / filename
        dst = dst_data / filename
        if not src.exists():
            print(f"  skip  {filename} (not found in source)")
            skipped += 1
            continue
        if dst.exists():
            # Back up existing file before overwriting
            backup = dst.with_suffix(dst.suffix + f".bak_{int(time.time())}")
            if not dry_run:
                shutil.copy2(dst, backup)
            print(f"  copy  {filename}  (backed up existing to {backup.name})")
        else:
            print(f"  copy  {filename}")
        if not dry_run:
            shutil.copy2(src, dst)
        copied += 1

    for dirname in _PORTABLE_DIRS:
        src = src_data / dirname
        dst = dst_data / dirname
        if not src.exists():
            print(f"  skip  {dirname}/  (not found in source)")
            skipped += 1
            continue
        if dst.exists():
            backup = dst_data / (dirname + f"_bak_{int(time.time())}")
            if not dry_run:
                shutil.copytree(dst, backup)
            print(f"  copy  {dirname}/  (backed up existing)")
        else:
            print(f"  copy  {dirname}/")
        if not dry_run:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        copied += 1

    print(f"\nMigration complete: {copied} items copied, {skipped} skipped.")
    if dry_run:
        print("Run without --dry-run to apply changes.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate portable Voyager experience to a new agent data directory."
    )
    parser.add_argument("--from", dest="src", type=Path,
                        default=_AGENT_DIR / "data",
                        help="Source data directory (default: agent/data/)")
    parser.add_argument("--to", dest="dst", type=Path, required=True,
                        help="Destination data directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be copied without doing anything")
    args = parser.parse_args()

    if not args.src.exists():
        print(f"Error: source directory '{args.src}' does not exist.", file=sys.stderr)
        sys.exit(1)

    migrate(args.src, args.dst, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.path.insert(0, str(_AGENT_DIR))
    main()
