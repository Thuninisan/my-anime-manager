"""Backfill only deterministic persisted facts. Run with python -m scripts.backfill_persistence."""
import logging

from backend.db.connection import new_session
from backend.db.persistence_migration import backfill


def main():
    logging.basicConfig(level=logging.INFO)
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        stats = backfill(session)
    logging.getLogger(__name__).info("Persistence backfill: %s", stats)


if __name__ == "__main__":
    main()
