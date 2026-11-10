"""Run the local backend in the foreground."""

import argparse
import logging

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(
        "backend.app:app",
        host="127.0.0.1",
        port=args.port,
        workers=1,
        proxy_headers=False,
        access_log=False,
        timeout_graceful_shutdown=15,
        limit_concurrency=64,
    )


if __name__ == "__main__":
    main()
