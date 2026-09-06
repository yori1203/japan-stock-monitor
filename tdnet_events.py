"""Offline TDnet normalization. Numbers must use the same unit and fiscal period.

Provider integrations map their fields to this schema before calling normalize_event.
No provider-specific URLs or credentials are needed by this layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime as DateTime, timedelta, timezone
from enum import Enum
import math
import re
import unicodedata
from typing import Mapping

JST = timezone(timedelta(hours=9))


class TDnetEventType(str, Enum):
    EARNINGS = "earnings"
    UPWARD_REVISION = "upward_revision"
    DOWNWARD_REVISION = "downward_revision"
    DIVIDEND_INCREASE = "dividend_increase"
    DIVIDEND_DECREASE = "dividend_decrease"
    SHARE_ISSUANCE = "share_issuance"
    WARRANT = "warrant"
    STOCK_OPTION = "stock_option"
    SHARE_BUYBACK = "share_buyback"
    CAPITAL_ALLIANCE = "capital_alliance"
    BUSINESS_ALLIANCE = "business_alliance"
    M_AND_A = "m_and_a"
    RESTRUCTURING = "restructuring"
    DELISTING_RELATED = "delisting_related"
    OTHER_MATERIAL = "other_material"


def timestamp(value=None):
    if value is None:
        return DateTime.now(timezone.utc)
    parsed = value if isinstance(value, DateTime) else DateTime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(unicodedata.normalize("NFKC", str(value)).replace(",", "").replace("△", "-").replace("▲", "-"))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, init=False)
class TDnetEvent:
    code: str
    published_at: DateTime
    event_type: TDnetEventType
    title: str
    source: str
    impact_score: float
    raw_reference: str | None
    company_name: str
    fiscal_period: str | None
    current_value: float | None
    previous_value: float | None
    change_rate: float | None
    confidence: float
    risk_flags: tuple[str, ...]
    positive_flags: tuple[str, ...]
    parsed_at: DateTime
    metric: str | None
    dilution_rate: float | None
    dilution_level: str | None
    use_of_proceeds: str | None
    growth_investment: bool | None
    buyback_rate: float | None

    def __init__(self, code, datetime=None, event_type=TDnetEventType.OTHER_MATERIAL,
                 title="", source="unknown", impact_score=50, raw_reference=None, *,
                 published_at=None, company_name="", fiscal_period=None, current_value=None,
                 previous_value=None, change_rate=None, confidence=0.5, risk_flags=(),
                 positive_flags=(), parsed_at=None, metric=None, dilution_rate=None,
                 dilution_level=None, use_of_proceeds=None, growth_investment=None, buyback_rate=None):
        # Preserve the original seven positional arguments and datetime keyword.
        values = locals().copy()
        values.pop("self")
        values.pop("datetime")
        values["published_at"] = timestamp(published_at if published_at is not None else datetime)
        values["parsed_at"] = timestamp(parsed_at)
        values["event_type"] = TDnetEventType(event_type)
        values["code"] = str(code)
        values["impact_score"] = max(0., min(100., number(impact_score) if number(impact_score) is not None else 50.))
        values["confidence"] = max(0., min(1., number(confidence) or 0.))
        for key in ("risk_flags", "positive_flags"):
            values[key] = tuple(dict.fromkeys(values[key]))
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def datetime(self):
        return self.published_at


METRICS = {
    "operating_profit": ("operating_profit", "operating_income", "営業利益"),
    "ordinary_profit": ("ordinary_profit", "経常利益"),
    "net_profit": ("net_profit", "net_income", "純利益"),
    "eps": ("eps", "EPS", "1株当たり当期純利益"),
    "revenue": ("revenue", "sales", "売上高"),
}


def forecast_values(raw: Mapping):
    forecasts = raw.get("forecasts") or {}
    # Select operating profit first; fallback follows business relevance, not dict order.
    for metric, aliases in METRICS.items():
        for alias in aliases:
            pair = forecasts.get(alias) or {}
            if isinstance(pair, Mapping):
                new, old = number(pair.get("current_value", pair.get("new"))), number(pair.get("previous_value", pair.get("old")))
                if new is not None and old is not None:
                    return metric, new, old
    metric = raw.get("metric")
    metric = next((name for name, aliases in METRICS.items() if metric in aliases), metric)
    return metric, number(raw.get("current_value")), number(raw.get("previous_value"))


def classify_event(raw: Mapping) -> TDnetEventType:
    explicit = raw.get("event_type")
    if explicit:
        return TDnetEventType(explicit)
    text = unicodedata.normalize("NFKC", f"{raw.get('title', '')} {raw.get('category', '')}").lower()
    text = re.sub(r"[\s・_－ー-]+", "", text)
    rules = [
        ("delisting_related", r"上場廃止|整理銘柄|監理銘柄"),
        ("restructuring", r"債務超過|民事再生|会社更生|事業再生|事業再構築|リストラ"),
        ("stock_option", r"ストックオプション|stockoption"),
        ("warrant", r"新株予約権|ワラント|warrant|転換社債|(?<![a-z])cb(?![a-z])"),
        ("share_issuance", r"第三者割当|公募増資|新株式発行|募集株式"),
        ("share_buyback", r"自社株買|自己株式.*取得|自己株取得"),
        ("dividend_decrease", r"減配|無配|配当.*(減額|引下|引き下)"),
        ("dividend_increase", r"増配|復配|配当.*(増額|引上|引き上)"),
    ]
    for kind, pattern in rules:
        if re.search(pattern, text):
            return TDnetEventType(kind)
    metric, new, old = forecast_values(raw)
    if "配当" in text and "修正" in text:
        if new is not None and old is not None and new != old:
            return TDnetEventType.DIVIDEND_INCREASE if new > old else TDnetEventType.DIVIDEND_DECREASE
        return TDnetEventType.OTHER_MATERIAL
    if re.search(r"下方修正|予想.*(引下|引き下)|赤字転落|利益予想.*減額", text):
        return TDnetEventType.DOWNWARD_REVISION
    if re.search(r"上方修正|利益予想.*増額|黒字転換", text):
        return TDnetEventType.UPWARD_REVISION
    if ("予想" in text and "修正" in text) or raw.get("forecasts") or metric:
        if new is not None and old is not None and new != old:
            return TDnetEventType.UPWARD_REVISION if new > old else TDnetEventType.DOWNWARD_REVISION
    for kind, pattern in [
        ("capital_alliance", r"資本.*提携"), ("business_alliance", r"業務提携|業務.*提携"),
        ("m_and_a", r"m&a|買収|子会社化|合併|株式交換|事業譲受"),
        ("earnings", r"決算短信|決算発表|四半期決算"),
    ]:
        if re.search(pattern, text):
            return TDnetEventType(kind)
    return TDnetEventType.OTHER_MATERIAL


def normalize_event(raw: Mapping, *, source="unknown", parsed_at=None) -> TDnetEvent:
    kind = classify_event(raw)
    metric, new, old = forecast_values(raw)
    rate = (new - old) / abs(old) if new is not None and old not in (None, 0) else None
    title = str(raw.get("title", ""))
    text = unicodedata.normalize("NFKC", title).lower()
    positive, risks = [], []
    score = 50.
    confidence = .75 if kind != TDnetEventType.OTHER_MATERIAL else .35
    if kind in (TDnetEventType.UPWARD_REVISION, TDnetEventType.DOWNWARD_REVISION):
        up = kind == TDnetEventType.UPWARD_REVISION
        # Same metric/period numeric evidence wins over a contradictory headline.
        if new is not None and old is not None:
            up = new > old
            kind = TDnetEventType.UPWARD_REVISION if up else TDnetEventType.DOWNWARD_REVISION
            confidence = .95
        strength = min(45., 15. + abs(rate) * 50.) if rate is not None else 20.
        if "大幅" in text:
            strength = max(strength, 35.)
        score = 50 + (strength if up else -strength)
        if up:
            positive.append("upward_revision")
            if metric == "operating_profit":
                positive.append("operating_profit_upgrade")
            if score >= 85:
                positive.append("strong_guidance")
        else:
            risks.extend(("downward_revision", "profit_warning"))
        if new is not None and old is not None and old <= 0 < new and metric != "revenue":
            score = 95.; positive.append("profit_turnaround")
        elif new is not None and old is not None and old >= 0 > new and metric != "revenue":
            score = 5.; risks.append("profit_warning")
        elif "黒字転換" in text:
            score = 95.; positive.append("profit_turnaround")
        elif "赤字転落" in text:
            score = 5.; risks.append("profit_warning")
        if new is not None and old is not None and new == old:
            kind = TDnetEventType.OTHER_MATERIAL
            score = 50.; positive.clear(); risks.clear()
    base = {
        "dividend_increase": (75, "dividend_increase", None),
        "dividend_decrease": (25, None, "dividend_cut"),
        "share_buyback": (75, "share_buyback", None),
        "capital_alliance": (65, "capital_alliance", None),
        "business_alliance": (60, "business_alliance", None),
        "m_and_a": (55, None, None),
        "restructuring": (15, None, "restructuring_risk"),
        "delisting_related": (0, None, "delisting_risk"),
    }
    if kind.value in base:
        score, pos, risk = base[kind.value]
        if pos: positive.append(pos)
        if risk: risks.append(risk)
    new_shares, existing = number(raw.get("new_shares")), number(raw.get("existing_shares"))
    dilution = new_shares / existing if new_shares is not None and new_shares >= 0 and existing is not None and existing > 0 else None
    level = None
    if kind in (TDnetEventType.SHARE_ISSUANCE, TDnetEventType.WARRANT, TDnetEventType.STOCK_OPTION):
        risks.append("dilution")
        level = "unknown" if dilution is None else "minor" if dilution <= .03 else "caution" if dilution < .10 else "high" if dilution < .20 else "severe"
        score = {"unknown": 30, "minor": 40, "caution": 30, "high": 15, "severe": 5}[level]
        if dilution is not None and dilution >= .10: risks.append("major_dilution")
        if kind == TDnetEventType.WARRANT: risks.append("warrant")
        if re.search(r"ms[\s・-]*(ワラント|warrant)|行使価額修正", text):
            risks.append("ms_warrant"); score = 5
    buyback = number(raw.get("buyback_rate"))
    if kind == TDnetEventType.SHARE_BUYBACK and ((buyback is not None and buyback >= .05) or re.search(r"大規模|大型", text)):
        score = 90; positive.append("large_buyback")
    return TDnetEvent(
        raw["code"], published_at=raw["published_at"], event_type=kind, title=title,
        source=raw.get("source", source), raw_reference=raw.get("raw_reference"),
        company_name=raw.get("company_name", ""), fiscal_period=raw.get("fiscal_period"),
        current_value=new, previous_value=old, change_rate=rate, metric=metric,
        impact_score=score, confidence=confidence, positive_flags=positive, risk_flags=risks,
        parsed_at=parsed_at, dilution_rate=dilution, dilution_level=level,
        use_of_proceeds=raw.get("use_of_proceeds"), growth_investment=raw.get("growth_investment"), buyback_rate=buyback,
    )
