"""Resume the frozen 50-symbol V3 measurement without repeating Yahoo work.

Exit 0: complete; 2: checkpoint saved, run another chunk; 1: invalid input.
Network operations run in disposable subprocesses with hard wall-clock limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from edinet_adapter import EdinetAdapter, EdinetConfig, EdinetFinancialData, USEFUL_DOC_TYPES

DEFAULT_INPUT = "validation/v3_edinet_50_input.json"
DEFAULT_CHECKPOINT = ".cache/v3-edinet-validation/full-checkpoint.json"
TERMINAL = {"ok", "no_recent_filing", "unmapped"}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_state(input_path, checkpoint):
    raw = Path(input_path).read_bytes()
    source = json.loads(raw)
    codes = [item["financial_data"]["code"] for item in source["candidates"]]
    if len(codes) != 50 or len(set(codes)) != 50:
        raise ValueError("Full validation requires exactly 50 unique symbols")
    # Windows checkout line endings must not invalidate an Actions checkpoint.
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True,
                                separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if Path(checkpoint).exists():
        state = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        if state.get("input_sha256") != fingerprint or state.get("version") != 1:
            raise ValueError("Checkpoint belongs to different input; use another checkpoint path")
    else:
        state = {"version": 1, "input_sha256": fingerprint, "as_of": source["as_of"],
                 "codes": codes, "next_offset": 0, "lookback_days": 190,
                 "documents": {}, "entries": {}, "results": {}, "errors": {},
                 "started_at": datetime.now(timezone.utc).isoformat()}
    return source, state


def worker(operation, cache, argument):
    adapter = EdinetAdapter(cache_dir=cache, config=EdinetConfig(
        timeout=12, max_retries=1, retry_delay=1, rate_limit_delay=.2))
    if operation == "map":
        return {code: asdict(entry) for code, entry in adapter.fetch_code_map().items()}
    if operation == "day":
        return adapter._decode_api_json(adapter._get("documents.json", {"date": argument, "type": "2"}))
    if operation == "document":
        value = json.loads(argument)
        result = adapter.fetch_document(value["code"], value["document"])
        return asdict(result)
    raise ValueError("Unknown operation")


def bounded_call(operation, cache, argument, timeout):
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", operation,
             "--cache", str(cache), "--argument", argument],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"{operation} exceeded wall-clock limit") from None
    if result.returncode:
        # Never propagate child stderr or URLs containing a subscription key.
        raise RuntimeError(f"{operation} worker failed")
    return json.loads(result.stdout)


def select_documents(state, listing):
    wanted = {entry["edinet_code"]: code for code, entry in state["entries"].items()}
    # Same-day submissions are not necessarily returned in time order.
    for document in sorted(listing.get("results", []),
                           key=lambda item: item.get("submitDateTime", ""), reverse=True):
        code = wanted.get(document.get("edinetCode"))
        if (code and code not in state["documents"]
                and str(document.get("docTypeCode")) in USEFUL_DOC_TYPES
                and str(document.get("xbrlFlag")) == "1"):
            state["documents"][code] = document


def usable_result(result, code, document):
    data = result.get("data") or {}
    return (result.get("status") == "ok" and data.get("code") == code
            and data.get("doc_id") == str(document["docID"])
            and bool(data.get("period_end"))
            and any(data.get(field) is not None for field in
                    ("revenue", "net_income", "total_assets", "equity")))


def complete(state):
    return all(state["results"].get(code, {}).get("status") in TERMINAL for code in state["codes"])


def run_chunk(input_path=DEFAULT_INPUT, checkpoint=DEFAULT_CHECKPOINT, *,
              cache=".cache/v3-edinet-validation/edinet", batch_size=10,
              max_scan_days=40, max_seconds=240, operation_timeout=45,
              call=bounded_call):
    if min(batch_size, max_scan_days, max_seconds, operation_timeout) <= 0:
        raise ValueError("All chunk limits must be positive")
    source, state = load_state(input_path, checkpoint)
    deadline = time.monotonic() + max_seconds
    cache = Path(cache)

    def save():
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(checkpoint, state)

    def invoke(operation, argument):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Chunk time budget exhausted")
        return call(operation, cache, argument, min(operation_timeout, remaining))

    if complete(state):
        print("Checkpoint already complete: 50/50; no acquisition repeated", flush=True)
        return state
    if not os.getenv("EDINET_API_KEY"):
        raise RuntimeError("EDINET_API_KEY is not configured")
    save()
    if not state.get("map_complete"):
        try:
            entries = invoke("map", "")
            if not entries:
                raise ValueError("Empty EDINET code map")
            state["entries"] = {code: entries[code] for code in state["codes"] if code in entries}
            state["map_complete"] = True
            state["errors"].pop("map", None)
            save()
        except (RuntimeError, TimeoutError, ValueError) as exc:
            state["errors"]["map"] = type(exc).__name__
            save()
            return state

    # Resume the exact as-of interval, even after midnight or a later session.
    for _ in range(max_scan_days):
        if (state["next_offset"] >= state["lookback_days"]
                or len(state["documents"]) == len(state["entries"])
                or time.monotonic() >= deadline):
            break
        day = (date.fromisoformat(state["as_of"]) - timedelta(days=state["next_offset"])).isoformat()
        path = cache / "document-lists" / f"{day}.json"
        try:
            try:
                listing = json.loads(path.read_text(encoding="utf-8"))
                if str(listing.get("metadata", {}).get("status", "200")) != "200":
                    raise ValueError("Invalid cached listing")
            except (OSError, ValueError):
                listing = invoke("day", day)
                atomic_json(path, listing)
            select_documents(state, listing)
            state["next_offset"] += 1
            state["errors"].pop("scan", None)
            save()
            if state["next_offset"] % 10 == 0:
                print(f"Search checkpoint: days={state['next_offset']} documents={len(state['documents'])}/50", flush=True)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            state["errors"]["scan"] = {"day": day, "reason": type(exc).__name__}
            save()
            break

    processed = 0
    scan_complete = state["next_offset"] >= state["lookback_days"]
    for code in state["codes"]:
        if state["results"].get(code, {}).get("status") in TERMINAL:
            continue
        if processed >= batch_size or time.monotonic() >= deadline:
            break
        document = state["documents"].get(code)
        if code not in state["entries"]:
            result = {"status": "unmapped", "reason": "EDINET code not found"}
        elif not document:
            if not scan_complete:
                continue
            result = {"status": "no_recent_filing", "reason": "No useful XBRL filing in the frozen 190-day interval"}
        else:
            try:
                raw = json.loads((cache / f"{code}.json").read_text(encoding="utf-8"))
                result = {"status": "ok", "data": raw["data"], "cache_hit": True}
                if not usable_result(result, code, document):
                    raise ValueError("Different or empty cached document")
                result["provenance"] = "reused_document_cache"
            except (OSError, KeyError, ValueError):
                try:
                    result = invoke("document", json.dumps({"code": code, "document": document}))
                    if not usable_result(result, code, document):
                        result = {"status": "error", "reason": "Document acquisition or financial extraction failed"}
                    result["provenance"] = "document_worker"
                except (RuntimeError, TimeoutError, ValueError) as exc:
                    result = {"status": "error", "reason": type(exc).__name__}
        state["results"][code] = result
        processed += 1
        save()
        done = sum(value.get("status") in TERMINAL for value in state["results"].values())
        print(f"Symbol checkpoint: {code} {result['status']} completed={done}/50", flush=True)
    print(f"Chunk finished: {dict(Counter(r['status'] for r in state['results'].values()))}", flush=True)
    return state


def render(input_path=DEFAULT_INPUT, checkpoint=DEFAULT_CHECKPOINT, output_dir="."):
    from financials import FinancialCandidate, FinancialData
    from financial_crosscheck import financial_crosscheck
    from final_ranking import rank_financial_candidates
    from final_report import FinalReportStats, write_final_report
    from v3_edinet_validation import FIELDS, build_report

    source, state = load_state(input_path, checkpoint)
    if not complete(state):
        raise ValueError("Cannot generate final ranking until all 50 symbols are validated")
    candidates, checks, rows, industries = [], {}, [], {}
    for raw in source["candidates"]:
        raw = dict(raw)
        raw["financial_data"] = FinancialData(**raw["financial_data"])
        candidate = FinancialCandidate(**raw)
        code = candidate.code
        entry = state["entries"].get(code, {})
        candidate = replace(candidate, company_name=entry.get("company_name", candidate.company_name))
        candidates.append(candidate)
        industries[code] = entry.get("industry") or source.get("industries", {}).get(code)
        result = state["results"][code]
        ed = EdinetFinancialData(**result["data"]) if result.get("data") else None
        yd = candidate.financial_data
        check = financial_crosscheck(yd, ed) if ed else None
        if check:
            checks[code] = check
        comparisons = {field.field: field for field in check.fields} if check else {}
        rows.append({"code": code, "company_name": candidate.company_name,
                     "edinet_code": entry.get("edinet_code", "未取得"),
                     "document_name": ed.document_name if ed else "未取得",
                     "period": f"{ed.period_start} - {ed.period_end}" if ed else "未取得",
                     "edinet_status": result["status"], "yahoo_status": "ok",
                     "failure_reason": result.get("reason"), "score": check.crosscheck_score if check else None,
                     "risks": check.edinet_risk_flags + check.warnings if check else (),
                     "missing": [f"EDINET:{name}" for name in FIELDS if getattr(ed, name, None) is None],
                     "matched": sum(f.status == "matched" for f in check.fields) if check else 0,
                     "warnings": sum(f.status == "warning" for f in check.fields) if check else 0,
                     "period_mismatch": bool(check and check.period_mismatch),
                     "unit_corrections": sum(f.unit_multiplier != 1 for f in check.fields) if check else 0,
                     "fields": [{"name": name, "edinet": getattr(ed, name, None),
                                 "yahoo": getattr(yd, name, None),
                                 "status": comparisons[name].status if name in comparisons else "unavailable"}
                                for name in FIELDS]})
    statuses = {code: result["status"] for code, result in state["results"].items()}
    ranked = rank_financial_candidates(candidates, crosschecks=checks, edinet_statuses=statuses, industries=industries)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = FinalReportStats(source["universe_count"], source["preselection_count"], len(candidates),
                             sum(s == "ok" for s in statuses.values()),
                             sum(c.financial_data_quality_score < 100 for c in candidates),
                             sum(len(c.warnings) for c in checks.values()))
    write_final_report(ranked, stats, out / "v3_final_candidates_report.md")
    report = build_report(rows, datetime.fromisoformat(state["started_at"]))
    report += (f"\n\n## 再開・入力情報\n\n- 固定基準日: {state['as_of']}\n"
               f"- 入力SHA256: {state['input_sha256']}\n- 対象: 取得済みYahoo財務50銘柄（再取得なし）\n"
               f"- 既存EDINETキャッシュ再利用数: {sum(r.get('provenance') == 'reused_document_cache' for r in state['results'].values())}\n"
               "- 書類なし・コード未対応は取得成功と区別。通信・抽出エラーは未完了として再開対象。\n")
    (out / "v3_edinet_full_validation_report.md").write_text(report, encoding="utf-8")
    atomic_json(out / "v3_edinet_full_validation_results.json", state)
    atomic_json(out / "v3_final_ranking.json", asdict(ranked))
    print(f"Full validation complete: targets=50 edinet_ok={stats.edinet_success_count} final={len(ranked.ranked_candidates)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--cache", default=".cache/v3-edinet-validation/edinet")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-scan-days", type=int, default=40)
    parser.add_argument("--max-seconds", type=float, default=240)
    parser.add_argument("--operation-timeout", type=float, default=45)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--worker", choices=("map", "day", "document"))
    parser.add_argument("--argument", default="")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.cache, args.argument), ensure_ascii=True))
        return 0
    if args.render:
        render(args.input, args.checkpoint, args.output_dir)
        return 0
    state = run_chunk(args.input, args.checkpoint, cache=args.cache, batch_size=args.batch_size,
                      max_scan_days=args.max_scan_days, max_seconds=args.max_seconds,
                      operation_timeout=args.operation_timeout)
    return 0 if complete(state) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Full validation stopped ({type(exc).__name__}); checkpoint retained", file=sys.stderr)
        sys.exit(1)
