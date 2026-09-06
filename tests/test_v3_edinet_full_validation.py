import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from v3_edinet_full_validation import (atomic_json, bounded_call, complete, load_state,
                                       run_chunk, select_documents, usable_result)


def fixture_input(tmp_path):
    path = tmp_path / 'input.json'
    atomic_json(path, {'as_of': '2026-09-05', 'candidates': [
        {'financial_data': {'code': str(1000 + i)}} for i in range(50)]})
    return path


def test_chunk_resume_skips_completed_symbols_and_days(tmp_path, monkeypatch):
    monkeypatch.setenv('EDINET_API_KEY', 'test-only')
    source = fixture_input(tmp_path)
    checkpoint = tmp_path / 'checkpoint.json'
    calls = []

    def call(operation, cache, argument, timeout):
        calls.append((operation, argument))
        if operation == 'map':
            return {str(1000 + i): {'edinet_code': f'E{i}'} for i in range(50)}
        if operation == 'day':
            return {'results': [{'edinetCode': f'E{i}', 'docID': f'D{i}',
                                 'docTypeCode': '120', 'xbrlFlag': '1'} for i in range(50)]}
        value = json.loads(argument)
        return {'status': 'ok', 'data': {'code': value['code'],
                'doc_id': value['document']['docID'], 'period_end': '2026-03-31', 'revenue': 1}}

    state = run_chunk(source, checkpoint, cache=tmp_path / 'cache', batch_size=10, call=call)
    assert len(state['results']) == 10
    assert state['next_offset'] == 1
    for _ in range(4):
        state = run_chunk(source, checkpoint, cache=tmp_path / 'cache', batch_size=10, call=call)
    assert complete(state)
    assert len([c for c in calls if c[0] == 'map']) == 1
    assert len([c for c in calls if c[0] == 'day']) == 1
    assert len([c for c in calls if c[0] == 'document']) == 50
    before = len(calls)
    monkeypatch.delenv('EDINET_API_KEY')
    run_chunk(source, checkpoint, call=call)
    assert len(calls) == before


def test_scan_timeout_does_not_advance_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('EDINET_API_KEY', 'test-only')
    source = fixture_input(tmp_path)
    checkpoint = tmp_path / 'checkpoint.json'

    def call(operation, *args):
        if operation == 'map':
            return {'1000': {'edinet_code': 'E0'}}
        raise TimeoutError('private-url-must-not-be-saved')

    state = run_chunk(source, checkpoint, cache=tmp_path, call=call)
    assert state['next_offset'] == 0
    assert state['errors']['scan']['reason'] == 'TimeoutError'
    assert 'private-url' not in checkpoint.read_text()
    assert not complete(state)


def test_changed_input_rejected(tmp_path):
    source = fixture_input(tmp_path)
    checkpoint = tmp_path / 'checkpoint.json'
    _, state = load_state(source, checkpoint)
    atomic_json(checkpoint, state)
    content = json.loads(source.read_text())
    content['as_of'] = '2026-09-04'
    atomic_json(source, content)
    with pytest.raises(ValueError, match='different input'):
        load_state(source, checkpoint)


def test_checkpoint_portable_across_line_endings(tmp_path):
    source = fixture_input(tmp_path)
    checkpoint = tmp_path / 'checkpoint.json'
    _, state = load_state(source, checkpoint)
    atomic_json(checkpoint, state)
    source.write_bytes(source.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert load_state(source, checkpoint)[1]['input_sha256'] == state['input_sha256']


def test_latest_same_day_document_selected():
    state = {'entries': {'1000': {'edinet_code': 'E0'}}, 'documents': {}}
    select_documents(state, {'results': [
        {'edinetCode': 'E0', 'docID': str(i), 'submitDateTime': t,
         'docTypeCode': '120', 'xbrlFlag': '1'}
        for i, t in enumerate(['2026-09-05 10:00', '2026-09-05 15:00'])]})
    assert state['documents']['1000']['docID'] == '1'


def test_empty_extraction_not_success():
    assert not usable_result({'status': 'ok', 'data': {'code': '1000', 'doc_id': 'D0'}},
                             '1000', {'docID': 'D0'})


def test_subprocess_timeout_kills_hanging_work(tmp_path):
    real_run = subprocess.run

    def hanging_run(command, **kwargs):
        return real_run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)

    started = time.monotonic()
    with patch('v3_edinet_full_validation.subprocess.run', side_effect=hanging_run):
        with pytest.raises(TimeoutError):
            bounded_call('day', tmp_path, '2026-09-05', .2)
    assert time.monotonic() - started < 5


def test_old_success_cache_reused_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv('EDINET_API_KEY', 'test-only')
    source = fixture_input(tmp_path)
    checkpoint = tmp_path / 'checkpoint.json'
    _, state = load_state(source, checkpoint)
    state.update(map_complete=True, entries={'1000': {'edinet_code': 'E0'}},
                 documents={'1000': {'docID': 'D0'}})
    atomic_json(checkpoint, state)
    atomic_json(tmp_path / '1000.json', {'fetched_at': '2026-09-05T12:55:00+00:00',
        'data': {'code': '1000', 'doc_id': 'D0', 'period_end': '2026-03-31', 'revenue': 5}})
    def forbidden(*args):
        raise AssertionError('Completed data was fetched again')
    state = run_chunk(source, checkpoint, cache=tmp_path, batch_size=1, call=forbidden)
    assert state['results']['1000']['provenance'] == 'reused_document_cache'
