"""Provider-neutral TDnet boundary; no key alone enables paid network I/O.

Injected loader(code, **kwargs) must map JPX/J-Quants fields to the canonical
schema before returning mappings. Transport remains outside this offline layer.
"""
from dataclasses import dataclass, field
import os
from tdnet_events import TDnetEvent, TDnetEventType, normalize_event, timestamp


@dataclass(frozen=True)
class TDnetResult:
    status: str
    events: tuple[TDnetEvent, ...] = ()
    reason: str | None = None
    provider_name: str = "unconfigured"
    fetched_at: str = field(default_factory=lambda: timestamp().isoformat())


class TDnetAdapter:
    def __init__(self, api_key=None, *, provider_name="unconfigured", loader=None):
        self.api_key = api_key or os.getenv("TDNET_API_KEY")
        self.provider_name = provider_name
        self.loader = loader

    def _unavailable(self):
        return TDnetResult("unavailable", reason="paid TDnet provider is not configured", provider_name=self.provider_name)

    def fetch_disclosures(self, code: str, **kwargs) -> TDnetResult:
        if self.loader is None:
            return self._unavailable()
        try:
            events = tuple(raw if isinstance(raw, TDnetEvent) else normalize_event(raw, source=self.provider_name)
                           for raw in self.loader(code, **kwargs))
            if any(e.code != str(code) for e in events):
                raise ValueError("provider returned a different stock")
            return TDnetResult("ok", events, provider_name=self.provider_name)
        except Exception:
            return TDnetResult("error", reason="provider fetch or normalization failed", provider_name=self.provider_name)

    def _filtered(self, code, kinds, **kwargs):
        result = self.fetch_disclosures(code, **kwargs)
        return TDnetResult(result.status, tuple(e for e in result.events if e.event_type.value in kinds),
                           result.reason, result.provider_name, result.fetched_at)

    def fetch_forecast_revisions(self, code: str, **kwargs):
        return self._filtered(code, {"upward_revision", "downward_revision"}, **kwargs)

    def fetch_dilution_events(self, code: str, **kwargs):
        return self._filtered(code, {"share_issuance", "warrant", "stock_option"}, **kwargs)

    def fetch_dividend_revisions(self, code: str, **kwargs):
        return self._filtered(code, {"dividend_increase", "dividend_decrease"}, **kwargs)


class JPXTDnetAdapter(TDnetAdapter):
    def __init__(self, api_key=None, *, loader=None):
        super().__init__(api_key, provider_name="jpx_tdnet", loader=loader)


class JQuantsTDnetAdapter(TDnetAdapter):
    def __init__(self, api_key=None, *, loader=None):
        super().__init__(api_key, provider_name="jquants_tdnet_addon", loader=loader)


class MockTDnetAdapter(TDnetAdapter):
    def __init__(self, events=None, *, as_of="2026-09-06T12:00:00+09:00"):
        super().__init__(provider_name="mock")
        if events is None:
            examples = [
                ("2146", "営業利益予想の大幅上方修正", {"metric": "operating_profit", "previous_value": 100, "current_value": 180}),
                ("8614", "MSワラントの発行", {"new_shares": 30, "existing_shares": 100}),
                ("2317", "配当予想の増額（増配）", {}),
                ("8572", "業績予想の大幅下方修正", {"metric": "operating_profit", "previous_value": 100, "current_value": 40}),
                ("8616", "大規模な自己株式取得", {"buyback_rate": .08}),
                ("8410", "第三者割当増資", {"new_shares": 5, "existing_shares": 100}),
                ("4406", "資本業務提携のお知らせ", {}),
            ]
            events = [dict(code=code, title=title, published_at=as_of, raw_reference=f"mock:{code}", **extra)
                      for code, title, extra in examples]
        self.events = tuple(e if isinstance(e, TDnetEvent) else normalize_event(e, source="mock", parsed_at=as_of) for e in events)

    def fetch_disclosures(self, code: str, **kwargs):
        return TDnetResult("mock", tuple(e for e in self.events if e.code == str(code)), provider_name="mock")
