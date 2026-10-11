"""Update community mappings directly in MAM_DATA_DIR/mam.sqlite3.

Usage: python scripts/download_bangumi_data.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.mapping_sync import synchronize
from backend.utils.http_client import http_client_manager


async def main() -> None:
    try:
        result = await synchronize()
        logging.getLogger(__name__).info("%s", result["output"])
    finally:
        await http_client_manager.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except Exception:
        logging.getLogger(__name__).exception("映射更新未完成")
        sys.exit(1)
