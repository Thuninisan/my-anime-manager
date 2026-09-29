"""Extract publisher descriptions from resource detail pages."""

from bs4 import BeautifulSoup


def extract_description(source: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = (
        ["article .entry-content", "article .post-content", "article .prose",
         "[itemprop='articleBody']", "article", "main article"]
        if source == "ktnbytes" else
        ["#torrent-description", ".panel-body#torrent-description"]
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get_text("\n", strip=True)
            if text:
                return text
    meta = soup.select_one("meta[name='description']")
    if meta:
        return (meta.get("content") or "").strip()
    return ""
