"""Entry point for daemon subprocess. Run with: python -m assistant_tools.tg.daemon_runner"""
import asyncio
import sys

from assistant_tools.config import load_config
from assistant_tools.tg.config import resolve_tg_config
from assistant_tools.tg.daemon import run_daemon


def main() -> None:
    config = load_config(None)
    tg_config = resolve_tg_config(config)
    asyncio.run(run_daemon(tg_config))


if __name__ == "__main__":
    main()
