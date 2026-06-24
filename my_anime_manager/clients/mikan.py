"""Mikanani.me HTML scraper — no official API, must parse web pages."""

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .. import config
from ..utils.http_retry import fetch_with_retry


async def get_subtitle_groups(mikan_id: int) -> list[dict]:
    """Scrape Mikan's Bangumi page and return all subtitle groups with RSS URLs.

    Args:
        mikan_id: Mikan's internal numeric ID for the anime.

    Returns:
        List of dicts with keys: name, subgroup_id, rss_url.
    """
    base = config.MIKAN_BASE_URL
    url = f"{base}/Home/Bangumi/{mikan_id}"

    resp = await fetch_with_retry(
        url,
        timeout=30.0,
        headers={"Accept": "text/html,application/xhtml+xml"},
        label=f"Mikan bangumi/{mikan_id}",
    )

    soup = BeautifulSoup(resp.text, "html.parser")
    groups: dict[int, dict] = {}  # dedup by subgroup_id

    # Strategy: find all RSS links on the page
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/RSS/Bangumi" not in href or "subgroupid" not in href:
            continue

        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        subgroup_ids = qs.get("subgroupid", [])
        if not subgroup_ids:
            continue
        subgroup_id = int(subgroup_ids[0])

        if subgroup_id in groups:
            continue

        # The group name is typically in the text of the link itself
        # or in an adjacent element. Try the link's own text first.
        name = a_tag.get_text(strip=True)
        if not name or name == "RSS":
            # The RSS link may be an icon with no text — look nearby
            # for a preceding <a> linking to /Home/PublishGroup/
            prev_a = a_tag.find_previous("a", href=True)
            if prev_a and "/Home/PublishGroup" in prev_a.get("href", ""):
                name = prev_a.get_text(strip=True)
            else:
                # Fallback: look for any text in the parent container
                parent_name = _find_group_name_in_parent(a_tag)
                name = parent_name or f"Subgroup {subgroup_id}"

        groups[subgroup_id] = {
            "name": name,
            "subgroup_id": subgroup_id,
            "rss_url": f"{base}/RSS/Bangumi?bangumiId={mikan_id}&subgroupid={subgroup_id}",
        }

    # Sort by subgroup_id for stable output
    return sorted(groups.values(), key=lambda g: g["subgroup_id"])


def _find_group_name_in_parent(a_tag) -> str | None:
    """Walk up a few levels looking for a group name text node."""
    current = a_tag.parent
    for _ in range(4):
        if current is None:
            break
        # Look for the PublishGroup link in this subtree
        pub_link = current.find("a", href=True)
        if pub_link and "/Home/PublishGroup" in pub_link.get("href", ""):
            text = pub_link.get_text(strip=True)
            if text:
                return text
        current = current.parent
    return None
