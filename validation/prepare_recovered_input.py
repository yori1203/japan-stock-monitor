"""Freeze existing measurement caches; does not call a data provider."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from financials import FinancialData, score_financial_candidate
from preselection import MarketSnapshot, score_preselection
from universe import UniverseSecurity


def prepare(cache=Path('.cache/recovery')):
    financials = json.loads((cache / 'financials.json').read_text(encoding='utf-8'))['financials']
    snapshots = json.loads((cache / 'preselection.json').read_text(encoding='utf-8'))['snapshots']
    universe = json.loads((cache / 'universe.json').read_text(encoding='utf-8'))['securities']
    securities = {item['code']: item for item in universe}
    reference = datetime(2026, 9, 5, 13, 18, 44, tzinfo=timezone.utc)
    candidates = []
    for code, raw in financials.items():
        security = UniverseSecurity(**securities[code])
        candidate = score_preselection(security, MarketSnapshot(**snapshots[code]))
        candidates.append(score_financial_candidate(candidate, FinancialData(**raw), now=reference))
    candidates.sort(key=lambda item: (-item.financial_score, -item.preselection_score, item.code))
    assert len(candidates) == 50
    payload = {'version': 1, 'as_of': '2026-09-05', 'measurement_at': reference.isoformat(),
               'source': 'Recovered v3-final-measure caches; Yahoo acquisition not repeated',
               'universe_count': 3707, 'preselection_count': 400,
               'metadata_note': 'Recovered JPX names contain encoding damage; EDINET code map supplies display names and industries.',
               'candidates': [asdict(item) for item in candidates]}
    Path('validation/v3_edinet_50_input.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print('Frozen 50 symbols:', ','.join(item.code for item in candidates))


if __name__ == '__main__':
    prepare()
