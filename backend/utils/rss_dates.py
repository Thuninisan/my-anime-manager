"""Date handling for RSS item publication times."""

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime


def publication_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def published_before_air_date(publication: str, air_date: str) -> bool:
    published = publication_datetime(publication)
    if not published or not air_date:
        return False
    try:
        premiere = date.fromisoformat(air_date)
    except ValueError:
        return False
    return published.date() < premiere
