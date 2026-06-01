"""Entry point for daemon subprocess. Run with: python -m assistant_tools.tg.daemon_runner"""
import asyncio
import sys

from assistant_tools.config import load_config
from assistant_tools.tg.config import resolve_tg_config
from assistant_tools.tg.daemon import run_daemon


def main() -> None:
    import traceback
    config = load_config(None)
    tg_config = resolve_tg_config(config)
    try:
        asyncio.run(run_daemon(tg_config))
    except Exception as e:
        import sys
        print(f"DAEMON CRASH: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
