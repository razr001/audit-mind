import argparse
import asyncio
import sys
from typing import Any, cast

import uvicorn


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AuditMind API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8181, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    # Uvicorn 运行时支持 loop factory，但当前公开类型只列出字符串别名。
    loop = cast(Any, selector_event_loop_factory) if sys.platform == "win32" else "auto"
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=loop,
    )


if __name__ == "__main__":
    main()
