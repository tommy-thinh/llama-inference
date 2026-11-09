"""Preview expired sessions and old conversations; deletion requires --apply."""

import argparse
import asyncio
import json

from backend.config import Settings
from backend.database import Database


async def run(days: int, apply: bool) -> dict:
    settings = Settings.from_environment()
    if not settings.database_url:
        raise ValueError("Set LLAMA_DATABASE_URL before running retention")
    database = Database(settings.database_url)
    try:
        return await database.retain(days=days, apply=apply)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=30, help="Inactive conversation age (default: 30)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Delete eligible rows and their children"
    )
    args = parser.parse_args()
    if args.days < 1:
        parser.error("days must be positive")
    try:
        result = asyncio.run(run(args.days, args.apply))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
