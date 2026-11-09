"""When is a deal "on"? The single rulebook, Python side.

Every part of Foodish that asks "does this deal count right now / today / on Friday?" calls this module,
and site/deal-state.js does the same in the browser. Both are checked against the same cases in
spec/deal-states.json, so they can't drift apart.

    from deal_state import deal_state, edmonton_now
    deal_state(deal, edmonton_now())          # -> "on_now", "later", "all_day", "ended", ...
    deal_state(deal, Moment("2026-11-13", 5)) # a future day with no time -> "scheduled" / "not_today"

States (also listed in spec/deal-states.json):
  expired      valid_until is before today
  upcoming     valid_from is after today
  not_today    doesn't run on this day
  all_day      runs today with no start/end time (never shown as "on now": opening hours unknown)
  later        runs today, starts later
  on_now       its window is open (including the after-midnight tail of yesterday's late deal)
  ended        ran earlier today and is over
  scheduled    runs on that day, when no time of day is given
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Edmonton")
EVERY_DAY = tuple(range(7))  # 0 = Sunday ... 6 = Saturday
MIDNIGHT = 24 * 60

EXPIRED, UPCOMING, NOT_TODAY = "expired", "upcoming", "not_today"
ALL_DAY, LATER, ON_NOW, ENDED, SCHEDULED = "all_day", "later", "on_now", "ended", "scheduled"
STATES = (EXPIRED, UPCOMING, NOT_TODAY, ALL_DAY, LATER, ON_NOW, ENDED, SCHEDULED)
VISIBLE = (ON_NOW, LATER, ALL_DAY, ENDED, SCHEDULED)  # states worth showing someone

_HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


@dataclass(frozen=True)
class Moment:
    """An Edmonton wall-clock moment. mins=None means 'that day, no particular time'."""
    date: str          # "YYYY-MM-DD"
    day: int           # 0 = Sunday ... 6 = Saturday
    mins: int | None = None


# Alberta stopped changing its clocks on 1 Nov 2026 and stays on UTC-6 year-round. Many computers' and
# phones' time-zone data still expect standard time (UTC-7) from November, which would put every
# "on now" an hour off, so after that date we use the fixed offset instead of trusting local tz data.
PERMANENT_UTC6_FROM = dt.datetime(2026, 11, 1, 8, 0, tzinfo=dt.timezone.utc)  # 2 am MDT, the old fall-back moment
UTC6 = dt.timezone(dt.timedelta(hours=-6))


def edmonton_time(now: dt.datetime | None = None) -> dt.datetime:
    t = now or dt.datetime.now(dt.timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(UTC6 if t >= PERMANENT_UTC6_FROM else TZ)


def edmonton_now(now: dt.datetime | None = None) -> Moment:
    t = edmonton_time(now)
    return Moment(t.date().isoformat(), (t.weekday() + 1) % 7, t.hour * 60 + t.minute)


def moment_from_local(text: str) -> Moment:
    """'2026-11-11T17:40' or '2026-11-11' (Edmonton wall clock) -> Moment. Used by the test cases."""
    date, _, clock = text.partition("T")
    d = dt.date.fromisoformat(date)
    mins = None
    if clock:
        h, m = clock.split(":")[:2]
        mins = int(h) * 60 + int(m)
    return Moment(date, (d.weekday() + 1) % 7, mins)


def to_mins(hhmm) -> int | None:
    if not isinstance(hhmm, str):
        return None
    m = _HHMM.fullmatch(hhmm.strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def days_of(deal: dict) -> tuple[int, ...]:
    days = [d for d in (deal.get("days") or []) if isinstance(d, int) and 0 <= d <= 6]
    return tuple(days) or EVERY_DAY


def runs_on(deal: dict, day: int) -> bool:
    return day in days_of(deal)


def is_live(deal: dict, date: str) -> bool:
    """Inside its valid_from / valid_until dates (both inclusive)."""
    if deal.get("valid_until") and deal["valid_until"] < date:
        return False
    if deal.get("valid_from") and deal["valid_from"] > date:
        return False
    return True


def deal_state(deal: dict, at: Moment) -> str:
    if deal.get("valid_until") and deal["valid_until"] < at.date:
        return EXPIRED
    if deal.get("valid_from") and deal["valid_from"] > at.date:
        return UPCOMING

    days = days_of(deal)
    start, end = to_mins(deal.get("start")), to_mins(deal.get("end"))
    overnight = start is not None and end is not None and end <= start

    if at.mins is None:
        return SCHEDULED if at.day in days else NOT_TODAY
    # after midnight: yesterday's late deal may still be running
    if overnight and (at.day + 6) % 7 in days and at.mins < end:
        return ON_NOW
    if at.day not in days:
        return NOT_TODAY
    if start is None and end is None:
        return ALL_DAY
    window_start = start if start is not None else 0
    window_end = MIDNIGHT if overnight or end is None else end
    if at.mins < window_start:
        return LATER
    if at.mins < window_end:
        return ON_NOW
    return ENDED
