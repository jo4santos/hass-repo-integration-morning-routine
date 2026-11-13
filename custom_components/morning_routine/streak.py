"""Streak of consecutive completed routine days (pure logic, no Home Assistant).

State kept per child (inside the child's stored data):

    streak            current number of consecutive completed routine days
    best_streak       best streak ever
    streak_last_date  ISO date of the last day the routine was completed
    streak_undo       snapshot taken when today was counted (so un-ticking an
                      activity the same day takes the day back)

Rules: a day counts once, when the routine is completed. With
``business_days_only`` the weekends are not routine days: they never break a
streak. A routine day that ends without a completed routine resets the streak
at the next reset.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

KEY_STREAK = "streak"
KEY_BEST = "best_streak"
KEY_LAST_DATE = "streak_last_date"
KEY_UNDO = "streak_undo"


def _as_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def is_routine_day(day: date, business_days_only: bool) -> bool:
    """Is this a day on which the routine is expected to be done?"""
    return not business_days_only or day.weekday() < 5


def previous_routine_day(day: date, business_days_only: bool) -> date:
    """Return the last routine day strictly before ``day``."""
    previous = day - timedelta(days=1)
    while not is_routine_day(previous, business_days_only):
        previous -= timedelta(days=1)
    return previous


def _is_alive(last: date | None, today: date, business_days_only: bool) -> bool:
    """A streak is alive when no routine day was missed since ``last``."""
    return last is not None and last >= previous_routine_day(today, business_days_only)


def current(child_data: dict[str, Any]) -> tuple[int, int]:
    """Return (streak, best_streak) of a child (0 when never tracked)."""
    streak = _as_int(child_data.get(KEY_STREAK))
    return streak, max(_as_int(child_data.get(KEY_BEST)), streak)


def on_completion(child_data: dict[str, Any], today: date, business_days_only: bool) -> bool:
    """Count today's completed routine. Returns True when the data changed."""
    last = _parse_date(child_data.get(KEY_LAST_DATE))
    if last is not None and last >= today:
        return False  # today already counted

    streak, best = current(child_data)
    child_data[KEY_UNDO] = {
        KEY_STREAK: streak,
        KEY_BEST: best,
        KEY_LAST_DATE: last.isoformat() if last else None,
        "date": today.isoformat(),
    }

    streak = streak + 1 if _is_alive(last, today, business_days_only) else 1
    child_data[KEY_STREAK] = streak
    child_data[KEY_BEST] = max(best, streak)
    child_data[KEY_LAST_DATE] = today.isoformat()
    return True


def on_uncompletion(child_data: dict[str, Any], today: date) -> bool:
    """Take today back when the routine stops being complete the same day."""
    undo = child_data.get(KEY_UNDO)
    if not isinstance(undo, dict) or undo.get("date") != today.isoformat():
        return False
    if child_data.get(KEY_LAST_DATE) != today.isoformat():
        return False

    child_data[KEY_STREAK] = _as_int(undo.get(KEY_STREAK))
    child_data[KEY_BEST] = _as_int(undo.get(KEY_BEST))
    child_data[KEY_LAST_DATE] = undo.get(KEY_LAST_DATE)
    child_data[KEY_UNDO] = None
    return True


def on_reset(child_data: dict[str, Any], today: date, business_days_only: bool) -> bool:
    """Apply the daily reset: a missed routine day ends the streak.

    A reset on a day already counted (manual reset after completing) keeps it.
    Returns True when the data changed.
    """
    last = _parse_date(child_data.get(KEY_LAST_DATE))
    streak, best = current(child_data)
    changed = False

    if not _is_alive(last, today, business_days_only) and streak != 0:
        streak = 0
        changed = True

    # The undo snapshot only makes sense on the day it was taken
    undo = child_data.get(KEY_UNDO)
    if undo is not None and not (isinstance(undo, dict) and undo.get("date") == today.isoformat()):
        child_data[KEY_UNDO] = None
        changed = True

    if child_data.get(KEY_STREAK) != streak or child_data.get(KEY_BEST) != best:
        changed = True
    child_data[KEY_STREAK] = streak
    child_data[KEY_BEST] = best
    return changed
