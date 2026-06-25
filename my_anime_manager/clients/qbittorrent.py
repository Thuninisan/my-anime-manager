"""qBittorrent WebUI API client using qbittorrent-api.

All network calls are run via ``asyncio.to_thread`` so that slow / hung
connections never block the event loop and Ctrl+C works immediately.
"""

import asyncio
import logging
from pathlib import Path

import qbittorrentapi
from qbittorrentapi import LoginFailed, Conflict409Error

logger = logging.getLogger(__name__)

# Seconds before giving up on a qBittorrent connection (connect + read).
_QB_TIMEOUT = 8


def _make_client(qb_url: str) -> qbittorrentapi.Client:
    """Create a configured client with short timeouts."""
    return qbittorrentapi.Client(
        host=qb_url,
        VERIFY_WEBUI_CERTIFICATE=False,
        REQUESTS_ARGS={"timeout": _QB_TIMEOUT},
    )


async def login(qb_url: str, username: str, password: str) -> qbittorrentapi.Client:
    """Login to qBittorrent and return an authenticated client.

    Runs the blocking ``auth_log_in`` call in a thread so a dead / slow
    qBittorrent host won't freeze the asyncio event loop.

    Args:
        qb_url: qBittorrent WebUI URL, e.g. http://192.168.18.68:8080
        username: qBittorrent username
        password: qBittorrent password

    Returns:
        Authenticated qbittorrentapi.Client instance

    Raises:
        RuntimeError: If login fails (connection refused, timeout, bad creds)
    """
    client = _make_client(qb_url)

    def _do_login():
        try:
            client.auth_log_in(username=username, password=password)
        except LoginFailed as e:
            raise RuntimeError(f"qBittorrent 登录失败: {e}") from e

    try:
        await asyncio.to_thread(_do_login)
    except RuntimeError:
        raise  # already formatted
    except Exception as e:
        msg = str(e).lower()
        if "connection" in msg or "refused" in msg or "timeout" in msg:
            raise RuntimeError(
                f"无法连接 qBittorrent ({qb_url}): 请确认 qBittorrent 已启动且地址正确"
            ) from e
        raise RuntimeError(f"qBittorrent 连接失败: {e}") from e

    return client


async def add_torrent(
    client: qbittorrentapi.Client,
    torrent_file_path: str,
    save_path: str,
    rename: str | None = None,
) -> str:
    """Add a torrent to qBittorrent in paused state.

    After adding, queries the torrent list by name to retrieve the info hash.

    Args:
        client: Authenticated qbittorrentapi.Client
        torrent_file_path: Path to .torrent file
        save_path: Download save path
        rename: Optional rename for the torrent (also used to look up the hash)

    Returns:
        Info hash string of the added torrent
    """
    torrent_name = rename or Path(torrent_file_path).stem

    def _do_add():
        # Snapshot hashes before adding so we can detect the new one
        before_hashes = {t.hash for t in client.torrents.info()}

        with open(torrent_file_path, "rb") as f:
            client.torrents.add(
                torrent_files=f,
                save_path=save_path,
                is_paused=True,
                is_stopped=True,
                use_auto_torrent_management=False,
                rename=rename,
            )

        # 1. Look for a newly appeared hash (fresh add)
        for torrent in client.torrents.info():
            if torrent.hash not in before_hashes:
                return torrent.hash

        # 2. If already existed (duplicate / re-add), match by name fragment
        for torrent in client.torrents.info():
            internal = torrent.name.lower().replace(" ", "")
            needle = torrent_name.lower().replace(" ", "")
            if needle in internal or internal in needle:
                return torrent.hash

        raise RuntimeError(f"添加种子后未找到 torrent: {torrent_name}")

    try:
        return await asyncio.to_thread(_do_add)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"添加种子失败: {e}") from e


async def get_torrent_files(
    client: qbittorrentapi.Client, info_hash: str
) -> list[dict]:
    """Get file list for a torrent.

    Args:
        client: Authenticated qbittorrentapi.Client
        info_hash: Torrent info hash

    Returns:
        List of dicts with 'name' key (compatible with parse_qbit_file_list)
    """
    def _do_get():
        files = client.torrents.files(torrent_hash=info_hash)
        return [{"name": f.name} for f in files]

    return await asyncio.to_thread(_do_get)


async def rename_file(
    client: qbittorrentapi.Client,
    info_hash: str,
    old_path: str,
    new_path: str,
) -> bool:
    """Rename a file within a torrent.

    Args:
        client: Authenticated qbittorrentapi.Client
        info_hash: Torrent info hash
        old_path: Current file path (relative to torrent root)
        new_path: New file path (relative to torrent root)

    Returns:
        True on success, False on conflict
    """
    def _do_rename():
        try:
            client.torrents.rename_file(
                torrent_hash=info_hash, old_path=old_path, new_path=new_path
            )
            return True
        except Conflict409Error:
            logger.warning("rename conflict: %s → %s", old_path, new_path)
            return False

    return await asyncio.to_thread(_do_rename)


async def resume_torrent(
    client: qbittorrentapi.Client, info_hash: str
) -> bool:
    """Resume a torrent download.

    Tries start first (for stopped state), then resume as fallback
    (for paused state / older qBittorrent versions).

    Args:
        client: Authenticated qbittorrentapi.Client
        info_hash: Torrent info hash

    Returns:
        True on success
    """
    def _do_resume():
        try:
            client.torrents.start(torrent_hashes=info_hash)
        except qbittorrentapi.APIError:
            pass  # start failed, fall through to resume

        client.torrents.resume(torrent_hashes=info_hash)
        return True

    return await asyncio.to_thread(_do_resume)


async def delete_torrent(
    client: qbittorrentapi.Client,
    info_hash: str,
    delete_files: bool = False,
) -> bool:
    """Delete a torrent from qBittorrent.

    Args:
        client: Authenticated qbittorrentapi.Client
        info_hash: Torrent info hash
        delete_files: Whether to also delete downloaded files

    Returns:
        True on success
    """
    def _do_delete():
        client.torrents.delete(torrent_hashes=info_hash, delete_files=delete_files)
        return True

    return await asyncio.to_thread(_do_delete)


async def get_torrents_by_hashes(
    client: qbittorrentapi.Client, hashes: list[str]
) -> dict[str, dict]:
    """Get torrent info for a set of info-hashes.

    Args:
        client: Authenticated qbittorrentapi.Client
        hashes: List of info-hash strings

    Returns:
        Dict mapping info_hash → {name, progress, state, size, ...}
        Empty dict if the torrent list API fails.
    """

    def _do_get():
        result = {}
        # Query all torrents; filter in Python (qBittorrent hash filter
        # is slow for many individual hashes)
        for t in client.torrents.info():
            if t.hash in hashes:
                result[t.hash] = {
                    "name": t.name,
                    "progress": t.progress,
                    "state": t.state,
                    "size": t.size,
                    "dlspeed": t.dlspeed,
                    "eta": t.eta,
                    "added_on": t.added_on,
                    "completion_on": t.completion_on,
                    "save_path": t.save_path,
                }
        return result

    try:
        return await asyncio.to_thread(_do_get)
    except Exception as e:
        logger.warning("get_torrents_by_hashes failed: %s", e)
        return {}
