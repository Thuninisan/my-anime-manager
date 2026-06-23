"""Compute BitTorrent info-hash from a .torrent file (bencode)."""

import hashlib

import bencodepy


def compute_info_hash(torrent_file_path: str) -> str:
    """Return the hex-encoded SHA1 info-hash of a .torrent file.

    Args:
        torrent_file_path: Path to the .torrent file.

    Returns:
        40-character hex info-hash string, or empty string on failure.
    """
    try:
        with open(torrent_file_path, "rb") as f:
            data = bencodepy.decode(f.read())
        info = data[b"info"]
        return hashlib.sha1(bencodepy.encode(info)).hexdigest()
    except Exception as e:
        print(f"   ⚠️ 计算 info_hash 失败: {e}")
        return ""
