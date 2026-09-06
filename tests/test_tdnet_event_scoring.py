from dataclasses import replace
from datetime import timedelta
import pytest
from tdnet_events import timestamp
from tdnet_event_scoring import time_decay, summarize_events, EventScoringConfig
from final_ranking import FinalRankingConfig, score_final_candidate, FINAL_SCORE_WEIGHTS
from tests.test_final_ranking import candidate
from tests.test_tdnet_events import event, NOW


@pytest.mark.parametrize("days,weight", [(0, 1), (3, 1), (4, .8), (7, .8), (8, .6), (14, .6), (15, .4), (30, .4), (31, .2), (180, .2), (-1, 0)])
def test_time_decay(days, weight):
    assert time_decay(timestamp(NOW) - timedelta(days=days), NOW) == weight


def test_unavailable_empty_future_and_duplicates():
    e = event("上方修正")
    assert summarize_events([e], status="unavailable", as_of=NOW).tdnet_event_score is None
    assert summarize_events([], as_of=NOW).tdnet_event_score == 50
    assert summarize_events([e, e], as_of=NOW) == summarize_events([e], as_of=NOW)
    assert summarize_events([replace(e, published_at=timestamp(NOW) + timedelta(days=1))], as_of=NOW).events == ()


def test_major_negative_dominates_small_announcements_and_overrides():
    bad = event("MSワラント")
    minor = [event(f"業務提携 {i}") for i in range(100)]
    s = summarize_events([bad, *minor], as_of=NOW)
    assert s.tdnet_event_score < 50 and s.adjustment < 0
    assert s == summarize_events([*reversed(minor), bad], as_of=NOW)


def test_decay_applies_to_override_and_direction():
    e = event("大幅上方修正")
    now = summarize_events([e], as_of=NOW)
    old = summarize_events([e], as_of=timestamp(NOW) + timedelta(days=31))
    assert 50 < old.tdnet_event_score < now.tdnet_event_score
    assert old.adjustment == round(now.adjustment * .2, 2)


def test_missing_tdnet_reweights_exactly():
    assert sum(FINAL_SCORE_WEIGHTS.values()) == 100
    item = candidate()
    # 15*75 + 35*80 + 15*90 + 10*85 + 10*60 + 5*100 over 90.
    assert score_final_candidate(item).final_score == round(7225 / 90, 2)
    assert score_final_candidate(item).tdnet_event_score is None
    assert "TDnet未照合" in score_final_candidate(item).warning_reasons


def test_material_override_config_and_clipping():
    item = candidate(tdnet_status="mock", tdnet_events=(event("大幅上方修正"),))
    off = FinalRankingConfig(event_scoring=EventScoringConfig(positive_cap=0, negative_cap=0))
    normal = score_final_candidate(item, generated_at=NOW)
    without = score_final_candidate(item, off, generated_at=NOW)
    assert normal.final_score - without.final_score == pytest.approx(6)
    bad = replace(item, tdnet_events=(event("MSワラント"),))
    assert score_final_candidate(bad, generated_at=NOW).final_score < without.final_score
    assert score_final_candidate(item, FinalRankingConfig(event_scoring=EventScoringConfig(positive_cap=1000)), generated_at=NOW).final_score == 100


def test_wrong_stock_rejected_and_config_validation():
    with pytest.raises(ValueError):
        score_final_candidate(candidate("9999", tdnet_events=(event("増配"),)), generated_at=NOW)
    with pytest.raises(ValueError):
        EventScoringConfig(positive_cap=float("nan"))


def test_dividend_positive_against_same_coverage_neutral():
    neutral = candidate(tdnet_status="mock")
    positive = replace(neutral, tdnet_events=(event("増配"),))
    assert score_final_candidate(positive, generated_at=NOW).final_score > score_final_candidate(neutral, generated_at=NOW).final_score
