"""Torrent file list parsing and filtering utilities."""

import re
from .parser import parse_filename

# =========== 过滤规则 ===========

OPED_PATTERNS = [
    re.compile(r"^nco?p\d", re.IGNORECASE),            # NCOP1, NCOP2 at start
    re.compile(r"^nced\d", re.IGNORECASE),              # NCED1, NCED2 at start
    re.compile(r"^nco?p[_\s]?v?\d", re.IGNORECASE),    # NCOP v2, NCOP_2 at start
    re.compile(r"^nced[_\s]?v?\d", re.IGNORECASE),     # NCED_2, NCED v2 at start
    re.compile(r"\bnco?p\d", re.IGNORECASE),            # NCOP2 anywhere
    re.compile(r"\bnced\d", re.IGNORECASE),             # NCED2 anywhere
    re.compile(r"\bnco?p[_\s]?v?\d", re.IGNORECASE),   # NCOP v2 anywhere
    re.compile(r"\bnced[_\s]?v?\d", re.IGNORECASE),    # NCED v2 anywhere
    re.compile(r"\bnco?p\b", re.IGNORECASE),            # standalone NCOP (no digit)
    re.compile(r"\bnced\b", re.IGNORECASE),             # standalone NCED (no digit)
    re.compile(r"opening", re.IGNORECASE),              # Opening
    re.compile(r"ending", re.IGNORECASE),               # Ending
    re.compile(r"creditless", re.IGNORECASE),           # Creditless OP/ED
]


def is_oped(filename: str) -> bool:
    """Check if a file is an OP/ED (opening/ending).

    Avoids matching "OP" standalone to prevent confusion with OPUS codec.

    Args:
        filename: The filename to check

    Returns:
        True if the file matches OP/ED patterns
    """
    base = re.sub(r"\.[^.]+$", "", filename)  # strip extension
    for pattern in OPED_PATTERNS:
        if pattern.search(base):
            return True
    return False


def is_special(filename: str) -> bool:
    """Check if a file is S00 (special/extra).

    Args:
        filename: The filename to check

    Returns:
        True if the filename contains S00
    """
    return bool(SPECIAL_PATTERN.search(filename))


def parse_qbit_file_list(
    file_list: list[dict], label: str = "qBittorrent"
) -> dict:
    """Parse qBittorrent file list into episodes and extras.

    Categorizes files into:
    - episodes: valid TV episodes with SXXEXX
    - extras: OP/ED files, specials (S00), unknown files

    Args:
        file_list: List of dicts with 'name' key from qBittorrent API
        label: Optional label for log output

    Returns:
        dict with 'episodes' and 'extras' lists
    """
    if not file_list:
        print("❌ qBittorrent 文件列表为空")
        return {"episodes": [], "extras": []}

    episodes = []
    extras = []
    skipped = {"oped": [], "novalid": []}

    for file in file_list:
        torrent_path = file["name"]  # full path within torrent
        filename = torrent_path.split("/")[-1] or torrent_path

        # Skip OP/ED
        if is_oped(filename):
            skipped["oped"].append(filename)
            extras.append({"fileName": filename, "torrentPath": torrent_path, "type": "oped"})
            continue

        # Try to extract SXXEXX (includes S00 specials)
        parsed = parse_filename(filename)
        if not parsed:
            skipped["novalid"].append(filename)
            extras.append({"fileName": filename, "torrentPath": torrent_path, "type": "unknown"})
            continue

        episodes.append({
            "fileName": filename,
            "torrentPath": torrent_path,
            "showName": parsed["showName"],
            "season": parsed["season"],
            "episode": parsed["episode"],
        })

    # Print filtering summary
    print(f"📁 解析种子文件列表 ({label}):")
    print(f"   总文件数: {len(file_list)}")
    print(f"   合规剧集: {len(episodes)}")
    print(f"   额外文件: {len(extras)}")
    if skipped["oped"]:
        print(f"   OP/ED: {len(skipped['oped'])} 个")
        for f in skipped["oped"]:
            print(f"     - {f}")
    if skipped["novalid"]:
        print(f"   ⚠️ 无法识别: {len(skipped['novalid'])} 个")
        for f in skipped["novalid"]:
            print(f"     - {f}")
    print("")

    return {"episodes": episodes, "extras": extras}
