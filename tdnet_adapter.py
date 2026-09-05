"""Provider-neutral TDnet interface.  No paid service is contacted yet."""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TDnetEventType(str, Enum):
    EARNINGS="earnings"; UPWARD_REVISION="upward_revision"; DOWNWARD_REVISION="downward_revision"
    DIVIDEND_INCREASE="dividend_increase"; DIVIDEND_DECREASE="dividend_decrease"
    SHARE_ISSUANCE="share_issuance"; STOCK_OPTION="stock_option"; WARRANT="warrant"
    SHARE_BUYBACK="share_buyback"; CAPITAL_ALLIANCE="capital_alliance"
    BUSINESS_ALLIANCE="business_alliance"; M_AND_A="m_and_a"; OTHER_MATERIAL="other_material"


@dataclass(frozen=True)
class TDnetEvent:
    code: str
    datetime: datetime
    event_type: TDnetEventType
    title: str
    source: str
    impact_score: float
    raw_reference: str | None = None


@dataclass(frozen=True)
class TDnetResult:
    status: str
    events: tuple[TDnetEvent,...]=()
    reason: str|None=None


class TDnetAdapter:
    """Base adapter for JPX TDnet API or the J-Quants TDnet add-on."""
    def __init__(self, api_key: str|None=None): self.api_key=api_key or os.getenv("TDNET_API_KEY")
    def _unavailable(self): return TDnetResult("unavailable",reason="paid TDnet provider is not configured")
    def fetch_disclosures(self, code: str, **kwargs) -> TDnetResult: return self._unavailable()
    def fetch_forecast_revisions(self, code: str, **kwargs) -> TDnetResult: return self._unavailable()
    def fetch_dilution_events(self, code: str, **kwargs) -> TDnetResult: return self._unavailable()
    def fetch_dividend_revisions(self, code: str, **kwargs) -> TDnetResult: return self._unavailable()
