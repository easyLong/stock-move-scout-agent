from __future__ import annotations

from datetime import date, datetime, time, timedelta


def _normal_dates(values: list[date | datetime | str] | None) -> list[date]:
    dates: list[date] = []
    for value in values or []:
        parsed: date | None = None
        if isinstance(value, datetime):
            parsed = value.date()
        elif isinstance(value, date):
            parsed = value
        else:
            text = str(value or "").strip()[:10]
            if text:
                try:
                    parsed = datetime.strptime(text, "%Y-%m-%d").date()
                except ValueError:
                    parsed = None
        if parsed and parsed not in dates:
            dates.append(parsed)
    return sorted(dates)


def previous_trade_date(reference_day: date, known_trade_dates: list[date | datetime | str] | None = None) -> date:
    """Return the previous A-share trading day before reference_day.

    If recent trading dates are supplied, prefer them so holiday gaps are handled
    by the local database. Otherwise fall back to weekday logic.
    """
    for day in reversed(_normal_dates(known_trade_dates)):
        if day < reference_day:
            return day

    day = reference_day - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def previous_trade_close_window(
    now: datetime | None = None,
    *,
    after_close_hour: int = 15,
    known_trade_dates: list[date | datetime | str] | None = None,
) -> tuple[date, datetime, datetime]:
    """Return (trade_date, since, until) for morning reference tasks."""
    current = now or datetime.now()
    trade_day = current.date()
    previous_day = previous_trade_date(trade_day, known_trade_dates)
    since = datetime.combine(previous_day, time(hour=int(after_close_hour), minute=0, second=0))
    return trade_day, since, current
