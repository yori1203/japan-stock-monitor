from dataclasses import asdict
import pytest
from tdnet_events import TDnetEvent, normalize_event, classify_event
from tdnet_adapter import MockTDnetAdapter, JPXTDnetAdapter, JQuantsTDnetAdapter

NOW = "2026-09-06T12:00:00+09:00"


def event(title, **extra):
    return normalize_event(dict(code="1000", title=title, published_at=NOW, **extra), parsed_at=NOW)


@pytest.mark.parametrize("title,kind", [
    ("業績予想の上方修正", "upward_revision"), ("営業利益予想増額", "upward_revision"),
    ("経常利益予想増額", "upward_revision"), ("通期予想引下げ", "downward_revision"),
    ("赤字転落予想", "downward_revision"), ("増配のお知らせ", "dividend_increase"),
    ("配当予想引き下げ", "dividend_decrease"), ("自社株買い", "share_buyback"),
    ("自己株式の取得", "share_buyback"), ("新株予約権発行", "warrant"),
    ("ＭＳワラント", "warrant"), ("転換社債", "warrant"), ("CB 発行", "warrant"),
    ("第三者割当増資", "share_issuance"), ("公募増資", "share_issuance"),
    ("ストック・オプションとしての新株予約権", "stock_option"),
    ("資本業務提携", "capital_alliance"), ("業務提携", "business_alliance"),
    ("子会社化のお知らせ", "m_and_a"), ("債務超過関連", "restructuring"),
    ("上場廃止のお知らせ", "delisting_related"), ("決算短信", "earnings"),
    ("通期業績予想修正", "other_material"), ("配当予想修正", "other_material"),
])
def test_title_classification(title, kind):
    assert event(title).event_type.value == kind


def test_schema_and_legacy_constructor():
    e = TDnetEvent("1000", datetime=__import__("datetime").datetime.fromisoformat(NOW), title="legacy")
    assert e.datetime == e.published_at
    assert {"code", "company_name", "published_at", "event_type", "title", "source", "raw_reference", "fiscal_period", "current_value", "previous_value", "change_rate", "impact_score", "confidence", "risk_flags", "positive_flags", "parsed_at"} <= asdict(e).keys()


def test_operating_profit_priority_and_generic_revision():
    e = event("通期業績予想修正", forecasts={"revenue": {"new": 90, "old": 100}, "営業利益": {"new": 160, "old": 100}})
    assert e.event_type.value == "upward_revision" and e.change_rate == .6
    assert e.impact_score == 95 and "operating_profit_upgrade" in e.positive_flags


@pytest.mark.parametrize("new,old,flag,score", [(10, -10, "profit_turnaround", 95), (-10, 10, "profit_warning", 5), (10, 0, "profit_turnaround", 95), (-10, 0, "profit_warning", 5)])
def test_sign_transitions(new, old, flag, score):
    e = event("通期業績予想修正", metric="operating_profit", current_value=new, previous_value=old)
    assert flag in (*e.positive_flags, *e.risk_flags) and e.impact_score == score
    if old == 0: assert e.change_rate is None


def test_loss_narrowing_and_flat_numeric_evidence():
    assert event("通期業績予想修正", current_value=-5, previous_value=-10).change_rate == .5
    assert event("業績予想の上方修正", current_value=0, previous_value=0).impact_score == 50


@pytest.mark.parametrize("shares,level", [(3, "minor"), (3.1, "caution"), (10, "high"), (20, "severe")])
def test_dilution_boundaries(shares, level):
    e = event("第三者割当増資", new_shares=shares, existing_shares=100, use_of_proceeds="設備投資", growth_investment=True)
    assert e.dilution_rate == shares / 100 and e.dilution_level == level
    assert ("major_dilution" in e.risk_flags) == (shares >= 10)
    assert e.use_of_proceeds == "設備投資" and e.growth_investment is True


@pytest.mark.parametrize("old", [0, -1, None, "NaN", "inf"])
def test_invalid_share_denominator(old):
    assert event("新株予約権", new_shares=10, existing_shares=old).dilution_rate is None


def test_ms_warrant_and_large_stock_option():
    assert "ms_warrant" in event("ＭＳワラント").risk_flags
    assert event("ＭＳワラント").impact_score == 5
    assert "major_dilution" in event("ストックオプション", new_shares=25, existing_shares=100).risk_flags


def test_dividend_direction_numeric_and_category():
    assert event("配当予想修正", current_value=12, previous_value=10).event_type.value == "dividend_increase"
    assert classify_event({"category": "業績予想の下方修正"}).value == "downward_revision"


def test_provider_contract_and_mock_filters():
    for cls in (JPXTDnetAdapter, JQuantsTDnetAdapter):
        assert cls("unused-key").fetch_disclosures("1000").status == "unavailable"
        result = cls(loader=lambda code: [{"code": code, "title": "増配", "published_at": NOW}]).fetch_disclosures("1000")
        assert result.status == "ok" and result.fetched_at and result.events[0].source == result.provider_name
        assert cls(loader=lambda code: [{"code": "wrong"}]).fetch_disclosures("1000").status == "error"
    mock = MockTDnetAdapter()
    assert mock.fetch_forecast_revisions("2146").events
    assert mock.fetch_dilution_events("8614").events
    assert mock.fetch_dividend_revisions("2317").events
    assert not mock.fetch_dilution_events("2317").events
    assert MockTDnetAdapter([]).fetch_disclosures("1000").status == "mock"
