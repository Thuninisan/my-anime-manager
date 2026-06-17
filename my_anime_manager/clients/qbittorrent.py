"""qBittorrent WebUI API client using qbittorrent-api."""

from pathlib import Path

import qbittorrentapi
from qbittorrentapi import LoginFailed, Conflict409Error


async def login(qb_url: str, username: str, password: str) -> qbittorrentapi.Client:
    """Login to qBittorrent and return an authenticated client.

    Args:
        qb_url: qBittorrent WebUI URL, e.g. http://192.168.18.68:8080
        username: qBittorrent username
        password: qBittorrent password

    Returns:
        Authenticated qbittorrentapi.Client instance

    Raises:
        Exception: If login fails
    """
    client = qbittorrentapi.Client(host=qb_url)
    try:
        client.auth_log_in(username=username, password=password)
    except LoginFailed as e:
        raise Exception(f"qBittorrent 登录失败: {e}") from e
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

    raise Exception(f"添加种子后未找到 torrent: {torrent_name}")


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
    files = client.torrents.files(torrent_hash=info_hash)
    return [{"name": f.name} for f in files]


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
    try:
        client.torrents.rename_file(
            torrent_hash=info_hash, old_path=old_path, new_path=new_path
        )
        return True
    except Conflict409Error:
        print(f"   ⚠️ 文件重命名冲突: {old_path} → {new_path}")
        return False


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
    try:
        client.torrents.start(torrent_hashes=info_hash)
    except qbittorrentapi.APIError:
        pass  # start failed, fall through to resume

    client.torrents.resume(torrent_hashes=info_hash)
    return True
