"""Weekly anime broadcast schedule endpoints."""

from fastapi import APIRouter, HTTPException
from ..services.discovery import calendar

router = APIRouter()


@router.get("/api/explore/calendar")
async def explore_calendar() -> list[dict]:
    try:
        return await calendar.get_calendar()
    except calendar.CalendarUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
