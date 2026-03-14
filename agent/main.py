"""
Outward Voyager — Python agent entry point.
Run: py main.py
"""
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from orchestrator import Orchestrator


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("file", "./logs/voyager.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


async def main() -> None:
    config = load_config()
    setup_logging(config)
    logger = logging.getLogger("main")
    logger.info("Outward Voyager agent starting...")

    orchestrator = Orchestrator(config)
    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
