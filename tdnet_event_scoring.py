"""Deterministic, age-decayed aggregation with bounded material overrides."""
from dataclasses import dataclass
import math
from tdnet_events import JST, TDnetEvent, timestamp


@dataclass(frozen=True)
class EventScoringConfig:
    positive_cap: float = 8.0
    negative_cap: float = 15.0
    material_positive_threshold: float = 85.0
    material_negative_threshold: float = 15.0
    secondary_weight: float = .25

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.positive_cap, self.negative_cap,
                self.material_positive_threshold, self.material_negative_threshold, self.secondary_weight)):
            raise ValueError("event scoring settings must be finite")
        if self.positive_cap < 0 or self.negative_cap < 0 or not 0 <= self.secondary_weight <= 1:
            raise ValueError("invalid event scoring limits")
        if not 0 <= self.material_negative_threshold < 50 < self.material_positive_threshold <= 100:
            raise ValueError("material thresholds must straddle neutral")


@dataclass(frozen=True)
class TDnetEventSummary:
    status: str
    tdnet_event_score: float | None
    adjustment: float = 0.0
    events: tuple[TDnetEvent, ...] = ()
    positive_flags: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    as_of: str | None = None


def time_decay(published_at, as_of):
    now, published = timestamp(as_of).astimezone(JST), timestamp(published_at).astimezone(JST)
    days = (now.date() - published.astimezone(now.tzinfo).date()).days
    if published > now:
        return 0.
    return 1. if days <= 3 else .8 if days <= 7 else .6 if days <= 14 else .4 if days <= 30 else .2


def summarize_events(events, *, status="ok", as_of=None, config=EventScoringConfig()):
    now = timestamp(as_of)
    if status not in ("ok", "mock"):
        return TDnetEventSummary(status, None, as_of=now.isoformat())
    unique = {}
    for event in events:
        if event.published_at > now:
            continue
        key = (event.code, event.published_at, event.title, event.event_type)
        if key not in unique or event.confidence > unique[key].confidence:
            unique[key] = event
    recent = tuple(sorted(unique.values(), key=lambda e: (e.published_at, e.title), reverse=True))
    if len({e.code for e in recent}) > 1:
        raise ValueError("summarize_events expects one stock")
    deltas = sorted(((e.impact_score - 50) * time_decay(e.published_at, now) * e.confidence for e in recent), key=lambda x: (-abs(x), x))
    # Dominant event plus capped secondary evidence; volume cannot swamp severity.
    delta = deltas[0] if deltas else 0.
    if len(deltas) > 1:
        delta += max(-abs(delta), min(abs(delta), sum(deltas[1:]))) * config.secondary_weight
    positives, negatives = [], []
    for event in recent:
        weight = time_decay(event.published_at, now) * event.confidence
        if event.impact_score >= config.material_positive_threshold:
            positives.append(config.positive_cap * weight)
        if event.impact_score <= config.material_negative_threshold or "ms_warrant" in event.risk_flags or "major_dilution" in event.risk_flags:
            negatives.append(config.negative_cap * weight)
    adjustment = -max(negatives) if negatives else max(positives, default=0.)
    return TDnetEventSummary(status, round(max(0., min(100., 50 + delta)), 2), round(adjustment, 2), recent,
        tuple(dict.fromkeys(f for e in recent for f in e.positive_flags)),
        tuple(dict.fromkeys(f for e in recent for f in e.risk_flags)), now.isoformat())
