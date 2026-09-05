import unittest
from datetime import datetime,timezone
from tdnet_adapter import TDnetAdapter,TDnetEvent,TDnetEventType
class TDnetTests(unittest.TestCase):
    def test_unavailable(self):
        adapter=TDnetAdapter('')
        self.assertEqual(adapter.fetch_disclosures('1234').status,'unavailable')
        self.assertEqual(adapter.fetch_forecast_revisions('1234').status,'unavailable')
        self.assertEqual(adapter.fetch_dilution_events('1234').status,'unavailable')
        self.assertEqual(adapter.fetch_dividend_revisions('1234').status,'unavailable')
    def test_all_event_types_and_event(self):
        self.assertEqual(len(TDnetEventType),13)
        event=TDnetEvent('1234',datetime.now(timezone.utc),TDnetEventType.UPWARD_REVISION,'title','dummy',80,'ref')
        self.assertEqual(event.event_type.value,'upward_revision');self.assertEqual(event.impact_score,80)
