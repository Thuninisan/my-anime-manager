"""Read .torrent files directly via bencode — no qBittorrent needed.

The preview phase uses this to extract the file list from a .torrent file
so we can parse + match everything before ever touching qBittorrent.
"""

from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════
# Minimal bencode parser
# ═══════════════════════════════════════════════════════════════════════

def _decode_int(data: bytes, pos: int) -> tuple[int, int]:
    """Decode ``i<integer>e`` starting at *pos*.  Returns (value, new_pos)."""
    end = data.index(b"e", pos)
    return int(data[pos:end]), end + 1


def _decode_str(data: bytes, pos: int) -> tuple[bytes, int]:
    """Decode ``<length>:<bytes>`` starting at *pos*."""
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    start = colon + 1
    return data[start:start + length], start + length


def _decode(data: bytes, pos: int = 0):
    """Recursive bencode decoder.  Returns (value, new_pos)."""
    c = data[pos:pos + 1]
    if c == b"i":
        return _decode_int(data, pos + 1)
    if c == b"l":
        result = []
        pos += 1
        while data[pos:pos + 1] != b"e":
            item, pos = _decode(data, pos)
            result.append(item)
        return result, pos + 1
    if c == b"d":
        result = {}
        pos += 1
        while data[pos:pos + 1] != b"e":
            key, pos = _decode(data, pos)
            val, pos = _decode(data, pos)
            result[key.decode("utf-8", errors="replace")] = val
        return result, pos + 1
    if c.isdigit():
        return _decode_str(data, pos)
    raise ValueError(f"Unexpected bencode byte at {pos}: {c!r}")


def decode_bencode(data: bytes) -> dict:
    """Decode a bencoded .torrent file.  Returns the root dict."""
    result, _ = _decode(data)
    if not isinstance(result, dict):
        raise ValueError("Torrent root is not a dictionary")
    return result


# ═══════════════════════════════════════════════════════════════════════
# Torrent file reading
# ═══════════════════════════════════════════════════════════════════════

def read_torrent_file_list(torrent_path: str) -> list[dict]:
    """Read a .torrent file and return a qBittorrent-compatible file list.

    This produces the same output shape as ``get_torrent_files()`` so the
    downstream ``parse_qbit_file_list()`` works unchanged.

    Returns:
        List of ``{"name": str}`` dicts — one per file in the torrent.
        Paths use forward slashes and include the torrent's top-level
        directory name (matching qBittorrent's internal representation).
    """
    data = Path(torrent_path).read_bytes()
    torrent = decode_bencode(data)

    info = torrent.get("info")
    if not isinstance(info, dict):
        raise ValueError("Torrent is missing 'info' dict")

    top_name = _b2s(info.get("name", b"unknown"))

    files: list[dict] = []

    if "files" in info:
        # Multi-file torrent
        for f in info["files"]:
            if not isinstance(f, dict):
                continue
            path_parts = f.get("path", [])
            # Build the full path: top_name/subdir/.../filename
            segments = [top_name] + [_b2s(p) for p in path_parts if isinstance(p, bytes)]
            full_path = "/".join(segments)
            files.append({"name": full_path})
    else:
        # Single-file torrent
        name = _b2s(info.get("name", b"unknown"))
        files.append({"name": name})

    return files


def _b2s(val) -> str:
    """Decode a bencode byte-string value to str (UTF-8, replace errors)."""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)
