"""Offline TSE cash-market calendar, verified JPX 2026/2027 closures.

Source: https://www.jpx.co.jp/corporate/about-jpx/calendar/
Outside the verified years return unknown, never guess a trading calendar.
"""
from datetime import date, timedelta, time
from tdnet_events import JST, timestamp

CLOSURES = {
    2026: "01-01 01-02 01-03 01-12 02-11 02-23 03-20 04-29 05-03 05-04 05-05 05-06 07-20 08-11 09-21 09-22 09-23 10-12 11-03 11-23 12-31",
    2027: "01-01 01-02 01-03 01-11 02-11 02-23 03-21 03-22 04-29 05-03 05-04 05-05 07-19 08-11 09-20 09-23 10-11 11-03 11-23 12-31",
}


def trading_day(day):
    if day.year not in CLOSURES: raise ValueError("営業日カレンダーの検証対象年外")
    return day.weekday() < 5 and day.strftime("%m-%d") not in CLOSURES[day.year].split()


def latest_close(now):
    now = timestamp(now).astimezone(JST)
    day = now.date()
    if now.time() < time(15, 30): day -= timedelta(days=1)
    while not trading_day(day): day -= timedelta(days=1)
    return day


def price_freshness(values, now):
    result = dict(status="unknown", oldest=None, newest=None, warning="株価基準日不明", reason="株価基準日不明")
    try:
        dates = [date.fromisoformat(str(v)[:10]) for v in values if v]
        if not dates: return result
        result.update(oldest=str(min(dates)), newest=str(max(dates)))
        expected = latest_close(now)
        today = timestamp(now).astimezone(JST).date()
        if max(dates) > today or any(not trading_day(d) for d in dates):
            raise ValueError("株価基準日が未来または休場日")
        if min(dates) < expected:
            status, reason = "stale", f"必要な直近営業日終値 {expected} に対し最古の株価基準日は {min(dates)}"
        elif max(dates) > expected:
            status, reason = "acceptable", "当営業日の速報値を含む（終値未確定）"
        else:
            status, reason = "fresh", f"直近の確定営業日終値 {expected} と一致"
        result.update(status=status, reason=reason, warning=reason if status == "stale" else "なし")
    except (ValueError, TypeError) as exc:
        result.update(reason=str(exc), warning=str(exc))
    return result


def age_freshness(values, now, max_days):
    dates = []
    for v in values:
        try:
            if v: dates.append(timestamp(v))
        except (TypeError, ValueError): pass
    if not dates:
        return dict(status="unknown", oldest=None, newest=None, warning="取得日時・基準日不明", reason="取得日時・基準日不明")
    age = (timestamp(now) - min(dates)).total_seconds() / 86400
    status = "unknown" if max(dates) > timestamp(now) else "fresh" if age <= max_days / 2 else "acceptable" if age <= max_days else "stale"
    reason = "未来日時を含む" if status == "unknown" else f"最古データは{age:.1f}日前（許容{max_days}日）"
    return dict(status=status, oldest=min(dates).isoformat(), newest=max(dates).isoformat(), reason=reason,
                warning="古いデータを含む: " + reason if status == "stale" else reason if status == "unknown" else "なし")
