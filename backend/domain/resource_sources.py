"""Built-in resource-source definitions shared by persistence and feed parsing."""

SOURCES = {
    "ktnbytes": {"name": "KTNBytes", "rss_url": "https://ktnbytes.com/atom.xml",
                 "downloadtag": {"tag": "enclosure", "attribute": "url"}, "index_type": "tmdb"},
    "vcb-studio": {"name": "VCB-Studio", "rss_url": "https://nyaa.si/?page=rss&u=VCB-Studio",
                   "downloadtag": {"tag": "link", "attribute": None}, "index_type": "tvdb"},
}
