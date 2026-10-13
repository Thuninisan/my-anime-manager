"""Apply persisted settings and notify the owning background services."""

from ... import config
from ..subscriptions import worker as rss_worker
from ..resources import worker as resource_worker
from ..torrent import recovery as torrent_recovery


async def update(changes: dict[str, object]) -> dict:
    result = config.update(changes)
    if "RSS_POLL_INTERVAL_MIN" in changes:
        await rss_worker.apply_interval()
    if "RESOURCE_POLL_INTERVAL_MIN" in changes:
        resource_worker.notify_interval_changed()
    if changes.get("FONTINASS_ENABLED") and config.FONTINASS_ENABLED:
        await torrent_recovery.recover_pending()
    return result
