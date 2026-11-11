"""When is a deal "on"? The single rulebook, Python side.

Every part of Foodish that asks "does this deal count right now / today / on Friday?" calls this module,
and site/deal-state.js does the same in the browser. Both are checked against the same cases in
spec/deal-states.json, so they can't drift apart.

    from deal_state import deal_state, now_in
    deal_state(deal, now_in(city.timezone))   # -> "on_now", "later", "all_day", "ended", ...
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

EVERY_DAY = tuple(range(7))  # 0 = Sunday ... 6 = Saturday
MIDNIGHT = 24 * 60

EXPIRED, UPCOMING, NOT_TODAY = "expired", "upcoming", "not_today"
ALL_DAY, LATER, ON_NOW, ENDED, SCHEDULED = "all_day", "later", "on_now", "ended", "scheduled"
STATES = (EXPIRED, UPCOMING, NOT_TODAY, ALL_DAY, LATER, ON_NOW, ENDED, SCHEDULED)
VISIBLE = (ON_NOW, LATER, ALL_DAY, ENDED, SCHEDULED)  # states worth showing someone

_HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


@dataclass(frozen=True)
class Moment:
    """A wall-clock moment in the deal's own city. mins=None means 'that day, no particular time'."""
    date: str          # "YYYY-MM-DD"
    day: int           # 0 = Sunday ... 6 = Saturday
    mins: int | None = None


# Alberta and British Columbia both stopped changing their clocks in 2026. Many computers' and phones'
# time-zone data still expect the old November fall-back, which would put every "on now" an hour off,
# so from that moment we use each zone's fixed offset instead of trusting local tz data.
# Zones not listed here (e.g. America/Toronto, which still changes clocks) use normal tz data.
#   zone -> (the old fall-back moment in UTC, permanent offset in hours)
FIXED_OFFSETS = {
    "America/Edmonton": (dt.datetime(2026, 11, 1, 8, 0, tzinfo=dt.timezone.utc), -6),   # Alberta: UTC-6 all year
    "America/Vancouver": (dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc), -7),  # B.C.: UTC-7 all year
}
DEFAULT_ZONE = "America/Edmonton"


def local_time(zone: str = DEFAULT_ZONE, now: dt.datetime | None = None) -> dt.datetime:
    """The wall-clock time in a city's zone (e.g. "America/Vancouver")."""
    t = now or dt.datetime.now(dt.timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    fixed = FIXED_OFFSETS.get(zone)
    if fixed and t >= fixed[0]:
        return t.astimezone(dt.timezone(dt.timedelta(hours=fixed[1])))
    return t.astimezone(ZoneInfo(zone))


def now_in(zone: str = DEFAULT_ZONE, now: dt.datetime | None = None) -> Moment:
    """Right now in a city's zone, as a Moment for deal_state()."""
    t = local_time(zone, now)
    return Moment(t.date().isoformat(), (t.weekday() + 1) % 7, t.hour * 60 + t.minute)


# Edmonton shortcuts (the original names; still used where only Edmonton is involved)
def edmonton_time(now: dt.datetime | None = None) -> dt.datetime:
    return local_time("America/Edmonton", now)


def edmonton_now(now: dt.datetime | None = None) -> Moment:
    return now_in("America/Edmonton", now)


def moment_from_local(text: str) -> Moment:
    """'2026-11-11T17:40' or '2026-11-11' (the city's wall clock) -> Moment. Used by the test cases."""
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
