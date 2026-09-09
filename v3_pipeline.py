"""Production V3 pipeline: python v3_pipeline.py --mode cached.

full updates stale/missing data in bounded subprocesses; cached and quick never
acquire data. Reinvoke after exit 2 to resume; --new-run starts a new evaluation
while retaining acquisition caches. All outputs and checkpoints are V3-only.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from tdnet_events import JST, timestamp
from tdnet_provider import FreePublicTDnetProvider, production_results
from v3_edinet_full_validation import atomic_json, select_documents, usable_result
from v3_pipeline_report import build_report, freshness, portfolio_review, ranking_changes
from v3_freshness import price_freshness, age_freshness
from v3_portfolio import analyse_portfolio

STAGES = ("universe", "preselection", "financial", "edinet", "tdnet", "portfolio", "ranking", "report")
TERMINAL = {"completed", "cached", "skipped", "partial", "unavailable"}


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Paused(Exception):
    pass


def worker(operation, request):
    if operation == "universe":
        from universe import download_jpx_universe
        return [{**asdict(s), "fetched_at": s.fetched_at.isoformat()} for s in download_jpx_universe()]
    if operation == "quotes":
        from universe import UniverseSecurity
        from preselection import YFinanceBatchProvider
        return {k: asdict(v) for k, v in YFinanceBatchProvider(timeout=12).fetch_batch([UniverseSecurity(**s) for s in request]).items()}
    if operation == "financial":
        from financials import YahooFinanceAdapter
        return asdict(YahooFinanceAdapter().fetch(request))
    if operation.startswith("edinet_"):
        from v3_edinet_full_validation import worker as ed_worker
        return ed_worker(operation.removeprefix("edinet_"), request["cache"], request["argument"])
    raise ValueError("unknown worker")


class Pipeline:
    def __init__(self, *, mode="cached", root=None, cache_dir=None, output_dir=None,
                 new_run=False, max_seconds=180, max_operations=20, operation_timeout=30,
                 now=None, executor=None, before_stage=None):
        if mode not in {"full", "cached", "quick"}: raise ValueError("invalid mode")
        if min(max_seconds, max_operations, operation_timeout) <= 0: raise ValueError("invalid budget")
        self.root = Path(root or Path(__file__).resolve().parent)
        self.cache = Path(cache_dir or self.root / ".cache/v3-pipeline")
        self.out = Path(output_dir or self.root)
        self.mode, self.new_run = mode, new_run
        self.now = timestamp(now)
        self.max_seconds, self.max_operations = max_seconds, max_operations
        self.operation_timeout, self.executor, self.before_stage = operation_timeout, executor, before_stage
        self.work = self.cache / mode
        self.work.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.checkpoint = self.work / "checkpoint.json"
        self.source = read(self.root / "validation/v3_edinet_50_input.json")
        self.saved_ed = read(self.root / "v3_edinet_full_validation_results.json", {})
        self.settings = read(self.root / "config.json", {})  # Read only: no V2 writes.
        self.operations = 0
        self.recovery = self.root / ".cache/recovery"

    def save(self):
        atomic_json(self.checkpoint, self.state)

    def artifact(self, stage):
        return self.work / f"{stage}.json"

    def result(self, stage):
        value = read(self.artifact(stage))
        if digest(value) != self.state["stages"][stage].get("output_hash"):
            raise ValueError(f"{stage} checkpoint artifact changed or missing")
        return value

    def call(self, operation, request):
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if self.operations >= self.max_operations or remaining < 1: raise Paused("operation budget reached")
        self.operations += 1
        if self.executor:
            return self.executor(operation, request)
        inp, out = self.work / "worker-input.json", self.work / "worker-output.json"
        atomic_json(inp, request)
        out.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", operation,
            "--request", str(inp.resolve()), "--response", str(out.resolve())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=min(self.operation_timeout, remaining), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if result.returncode or not out.exists(): raise RuntimeError(f"{operation} worker failed")
        return read(out)

    def run(self):
        self.started = time.monotonic()
        lock = self.cache / "pipeline.lock"
        try:
            with lock.open("x") as handle: handle.write(self.now.isoformat())
        except FileExistsError:
            raise RuntimeError("V3 pipeline lock exists; inspect before resuming") from None
        try:
            fingerprint = digest({"version": 2, "source": self.source, "edinet": self.saved_ed, "config": self.settings, "mode": self.mode,
                "portfolio_settings": read(self.root / "v3_portfolio_settings.json"), "portfolio_input": read(self.root / "validation/v3_portfolio_input.json"),
                "portfolio_cache": read(self.cache / "portfolio-cache.json")})
            previous = read(self.checkpoint)
            if previous and not self.new_run:
                if previous["input_hash"] != fingerprint: raise ValueError("inputs changed; use --new-run (acquisition caches retained)")
                self.state = previous
                self.now = timestamp(previous["started_at"])
            else:
                baseline = read(self.out / "v3_final_ranking.json", read(self.root / "v3_final_ranking.json", {}))
                atomic_json(self.work / "previous-ranking.json", baseline)
                self.state = {"version": 1, "mode": self.mode, "input_hash": fingerprint, "started_at": self.now.isoformat(),
                    "status": "running", "warnings": [], "stages": {s: {"status": "pending", "started_at": None,
                    "completed_at": None, "input_count": 0, "success_count": 0, "failure_count": 0, "cache_hits": 0} for s in STAGES}}
                # Per-operation progress is retained on resume, cleared only for a new run.
                for name in ("quote-progress", "financial-progress", "edinet-progress"):
                    (self.work / f"{name}.json").unlink(missing_ok=True)
                self.save()
            for name in STAGES:
                meta = self.state["stages"][name]
                if meta["status"] in TERMINAL:
                    output = self.result(name)
                    if name != "report" or all((self.out / path).exists() and digest((self.out / path).read_text(encoding="utf-8")) == expected for path, expected in output.get("outputs", {}).items()):
                        continue
                    meta["status"] = "pending"
                meta.update(status="running", started_at=meta["started_at"] or timestamp().isoformat(), completed_at=None)
                meta.pop("error", None)
                self.state["status"] = "running"
                self.save()
                try:
                    if self.before_stage: self.before_stage(name)
                    payload, stats = getattr(self, "stage_" + name)()
                    atomic_json(self.artifact(name), payload)
                    meta.update(stats, completed_at=timestamp().isoformat(), output_hash=digest(payload))
                    self.save()
                except Exception as exc:
                    meta["status"] = "paused" if isinstance(exc, Paused) else "failed"
                    meta["failure_count"] = max(meta["failure_count"], 0 if isinstance(exc, Paused) else 1)
                    meta["error"] = type(exc).__name__  # No credentials or provider URLs in errors.
                    self.state["status"] = meta["status"]
                    self.state["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
                    self.save()
                    atomic_json(self.out / "v3_pipeline_run.json", self.state)
                    return self.state
            self.state["status"] = "completed"
            self.state["elapsed_seconds"] = self.state.get("elapsed_seconds", round(time.monotonic() - self.started, 3))
            self.save()
            atomic_json(self.out / "v3_pipeline_run.json", self.state)
            return self.state
        finally:
            lock.unlink(missing_ok=True)

    def stats(self, count, success, hits=0, failures=0, status=None):
        return dict(status=status or ("partial" if failures else "completed"), input_count=count,
                    success_count=success, failure_count=failures, cache_hits=hits)

    def progress(self, stage, count, success, hits=0, failures=0):
        self.state["stages"][stage].update(input_count=count, success_count=success,
                                           cache_hits=hits, failure_count=failures)
        self.save()

    def stage_universe(self):
        payload = read(self.cache / "universe-cache.json", read(self.recovery / "universe.json"))
        if self.mode == "quick":
            return {"securities": [], "count": self.source["universe_count"], "fetched_at": payload.get("fetched_at") if payload else None}, self.stats(self.source["universe_count"], 0, status="skipped")
        if self.mode == "full" and (not payload or freshness([payload.get("fetched_at")], self.now, 7)["warning"] != "なし"):
            securities = self.call("universe", {})
            if not securities: raise ValueError("empty JPX universe")
            payload = {"securities": securities, "fetched_at": self.now.isoformat()}
            atomic_json(self.cache / "universe-cache.json", payload)
            hits = 0
        else:
            hits = len(payload["securities"]) if payload else 0
        if not payload:
            self.state["warnings"].append("JPX全銘柄キャッシュなし。固定入力の過去件数のみ参照")
            return {"securities": [], "count": self.source["universe_count"], "fetched_at": None}, self.stats(self.source["universe_count"], 0, status="unavailable")
        return {**payload, "count": len(payload["securities"])}, self.stats(len(payload["securities"]), len(payload["securities"]), hits, status="cached" if hits else "completed")

    def stage_preselection(self):
        from universe import UniverseSecurity, filter_equity_universe
        from preselection import MarketSnapshot, PreselectionConfig, score_preselection, exclusion_reason
        uni = self.result("universe")
        cached = read(self.cache / "quotes-cache.json", read(self.recovery / "preselection.json", {}))
        snapshots = cached.get("snapshots", {})
        if self.mode == "quick" or not uni["securities"]:
            return {"selected": [], "snapshots": snapshots, "count": self.source["preselection_count"]}, self.stats(self.source["preselection_count"], 0, status="skipped")
        securities = filter_equity_universe([UniverseSecurity(**s) for s in uni["securities"]])
        progress_path = self.work / "quote-progress.json"
        progress = read(progress_path, {"done": [], "failures": {}})
        hits = sum(s.code in snapshots and (self.mode != "full" or price_freshness([snapshots[s.code].get("data_as_of")], self.now)["status"] in {"fresh", "acceptable"}) for s in securities)
        self.progress("preselection", len(securities), len(progress["done"]), hits, len(progress["failures"]))
        if self.mode == "full":
            missing = [s for s in securities if s.code not in progress["done"] and
                (s.code not in snapshots or price_freshness([snapshots[s.code].get("data_as_of")], self.now)["status"] not in {"fresh", "acceptable"})]
            for offset in range(0, len(missing), 50):
                batch = missing[offset:offset+50]
                fetched = self.call("quotes", [asdict(s) for s in batch])
                for s in batch:
                    if s.code in fetched: snapshots[s.code] = fetched[s.code]
                    else: progress["failures"][s.code] = "quote unavailable"
                    progress["done"].append(s.code)
                atomic_json(self.cache / "quotes-cache.json", {"snapshots": snapshots})
                atomic_json(progress_path, progress)
                self.progress("preselection", len(securities), len(progress["done"]) - len(progress["failures"]), hits, len(progress["failures"]))
        config = PreselectionConfig(top_n=400)
        selected = []
        for s in securities:
            if s.code in snapshots:
                c = score_preselection(s, MarketSnapshot(**snapshots[s.code]))
                if exclusion_reason(c, config) is None: selected.append(c)
        selected.sort(key=lambda c: (-c.preselection_score, c.security.code))
        selected = selected[:400]
        return {"selected": [asdict(c) for c in selected], "snapshots": snapshots, "count": len(selected)}, self.stats(len(securities), len(selected), hits, len(progress["failures"]), status="cached" if self.mode == "cached" else None)

    def stage_financial(self):
        from financials import FinancialData, score_financial_candidate
        from preselection import PreselectedCandidate, MarketSnapshot
        from universe import UniverseSecurity
        pre = self.result("preselection")
        if self.mode != "full":
            latest = read(self.cache / "latest-financial.json")
            value = latest or {"candidates": self.source["candidates"], "industries": {}, "count": len(self.source["candidates"])}
            return value, self.stats(pre["count"], value["count"], value["count"], status="cached")
        saved = {c["code"]: c["financial_data"] for c in self.source["candidates"]}
        saved.update(read(self.cache / "financial-cache.json", {}))
        progress_path = self.work / "financial-progress.json"
        progress = read(progress_path, {"done": [], "failures": {}})
        hits = 0
        prepared, industries = [], {}
        self.progress("financial", pre["count"], len(progress["done"]), failures=len(progress["failures"]))
        for raw in pre["selected"]:
            raw = dict(raw)
            raw["security"], raw["snapshot"] = UniverseSecurity(**raw["security"]), MarketSnapshot(**raw["snapshot"])
            c = PreselectedCandidate(**raw)
            code = c.security.code
            valid = code in saved and freshness([saved[code].get("fetched_at")], self.now, 7)["warning"] == "なし"
            if valid: hits += 1
            elif code not in progress["done"]:
                data = self.call("financial", code)
                if data and data.get("code") == code and data.get("fetched_at"): saved[code] = data
                else: progress["failures"][code] = "financial unavailable"
                progress["done"].append(code)
                atomic_json(self.cache / "financial-cache.json", saved)
                atomic_json(progress_path, progress)
            if code in saved:
                prepared.append(score_financial_candidate(c, FinancialData(**saved[code]), now=self.now))
                industries[code] = c.security.industry
            self.progress("financial", pre["count"], len(prepared), hits, len(progress["failures"]))
        prepared.sort(key=lambda c: (-c.financial_score, -c.preselection_score, c.code))
        if not prepared: raise ValueError("no financial candidates")
        value = {"candidates": [asdict(c) for c in prepared], "industries": industries, "count": len(prepared)}
        atomic_json(self.cache / "latest-financial.json", value)
        return value, self.stats(pre["count"], len(prepared), hits, len(progress["failures"]))

    def stage_edinet(self):
        candidates = self.result("financial")["candidates"]
        codes = [c["code"] for c in candidates[:50]]
        progress_path = self.work / "edinet-progress.json"
        progress = read(progress_path, {"results": {}, "entries": {}, "documents": {}, "next_offset": 0})
        hits = 0
        latest = read(self.cache / "edinet-cache.json", {})
        for code in codes:
            saved = latest.get("results", {}).get(code, self.saved_ed.get("results", {}).get(code, {}))
            valid = (saved.get("status") == "no_recent_filing" or
                     saved.get("status") == "ok" and saved.get("data", {}).get("code") == code and saved["data"].get("period_end"))
            at = saved.get("data", {}).get("fetched_at") or saved.get("checked_at") or self.saved_ed.get("as_of")
            if valid and (self.mode != "full" or freshness([at], self.now, 7)["warning"] == "なし"):
                progress["results"][code] = saved; hits += 1
                if code in self.saved_ed.get("entries", {}): progress["entries"][code] = self.saved_ed["entries"][code]
                if code in latest.get("entries", {}): progress["entries"][code] = latest["entries"][code]
        missing = [code for code in codes if code not in progress["results"]]
        self.progress("edinet", len(codes), sum(r.get("status") == "ok" for r in progress["results"].values()), hits)
        edcache = self.root / ".cache/v3-edinet-validation/edinet"
        def invoke(operation, argument):
            return self.call("edinet_" + operation, {"cache": str(edcache), "argument": argument})
        if missing and self.mode == "full" and os.getenv("EDINET_API_KEY"):
            if not progress.get("map_loaded"):
                mapping = invoke("map", "")
                progress["entries"].update({c: mapping[c] for c in missing if c in mapping})
                progress["map_loaded"] = True
                atomic_json(progress_path, progress)
            while progress["next_offset"] < 190 and any(c in progress["entries"] and c not in progress["documents"] for c in missing):
                day = (self.now.date() - timedelta(days=progress["next_offset"])).isoformat()
                path = edcache / "document-lists" / f"{day}.json"
                listing = read(path)
                if listing is None:
                    listing = invoke("day", day)
                    atomic_json(path, listing)
                select_documents(progress, listing)
                progress["next_offset"] += 1
                atomic_json(progress_path, progress)
            for code in missing:
                document = progress["documents"].get(code)
                if code not in progress["entries"]: value = {"status": "unavailable"}
                elif not document: value = {"status": "no_recent_filing"}
                else:
                    value = invoke("document", json.dumps({"code": code, "document": document}))
                    if not usable_result(value, code, document): value = {"status": "error"}
                progress["results"][code] = value
                value["checked_at"] = self.now.isoformat()
                atomic_json(progress_path, progress)
                self.progress("edinet", len(codes), sum(r.get("status") == "ok" for r in progress["results"].values()), hits,
                              sum(r.get("status") == "error" for r in progress["results"].values()))
        else:
            for code in missing: progress["results"][code] = {"status": "unavailable"}
        counts = Counter(r["status"] for r in progress["results"].values())
        self.state["edinet_counts"] = dict(counts)
        self.state["edinet_reference_date"] = self.saved_ed.get("as_of") if hits == len(codes) else self.now.date().isoformat()
        if self.mode == "full": atomic_json(self.cache / "edinet-cache.json", progress)
        return progress, self.stats(len(codes), counts["ok"], hits, counts["error"], status="cached" if hits == len(codes) else "partial" if missing else "completed")

    def stage_tdnet(self):
        codes = [c["code"] for c in self.result("financial")["candidates"]]
        # No permission reference: every mode fails closed, including full.
        result = FreePublicTDnetProvider().fetch_events_for_codes(codes, dates=[str(self.now.astimezone(JST).date())])
        self.state["tdnet_status"] = result.status
        self.state["warnings"].extend(result.warnings)
        mapped = production_results(result, codes)
        return {"status": result.status, "source": result.source, "fetched_at": result.fetched_at,
                "results": {c: asdict(r) for c, r in mapped.items()}}, self.stats(len(codes), 0, result.cache_hits, result.failure_count, status="unavailable")

    def stage_portfolio(self):
        financial = {c["code"]: c["financial_data"] for c in self.result("financial")["candidates"]}
        items = analyse_portfolio(self.root, self.cache, self.result("preselection")["snapshots"], financial,
                                  self.result("edinet")["results"], self.now)
        count = sum(p["portfolio_score"] is not None for p in items)
        return {"portfolio_universe": [p["code"] for p in items], "holdings": items}, self.stats(len(items), count, count,
            status="cached" if count == len(items) else "partial")

    def stage_ranking(self):
        from financials import FinancialCandidate, FinancialData
        from edinet_adapter import EdinetFinancialData
        from financial_crosscheck import financial_crosscheck
        from final_ranking import rank_financial_candidates
        from tdnet_adapter import TDnetResult
        from tdnet_events import TDnetEvent
        financial, ed, td = self.result("financial"), self.result("edinet"), self.result("tdnet")
        candidates, checks, industries = [], {}, dict(financial["industries"])
        for raw in financial["candidates"]:
            raw = dict(raw); raw["financial_data"] = FinancialData(**raw["financial_data"])
            c = FinancialCandidate(**raw)
            entry = ed["entries"].get(c.code, {})
            c = replace(c, company_name=entry.get("company_name", c.company_name))
            industries[c.code] = entry.get("industry") or industries.get(c.code)
            candidates.append(c)
            value = ed["results"].get(c.code, {})
            if value.get("status") == "ok" and value.get("data"):
                checks[c.code] = financial_crosscheck(c.financial_data, EdinetFinancialData(**value["data"]))
        tdresults = {code: TDnetResult(r["status"], tuple(TDnetEvent(**e) for e in r.get("events", [])), provider_name=r["provider_name"], fetched_at=r["fetched_at"]) for code, r in td["results"].items()}
        ranking = rank_financial_candidates(candidates, crosschecks=checks, industries=industries,
            edinet_statuses={c: r["status"] for c, r in ed["results"].items()}, tdnet_results=tdresults, generated_at=self.now.isoformat())
        value = asdict(ranking)
        value["report_stats"] = {"universe_count": self.source["universe_count"] if self.mode != "full" else self.result("universe")["count"],
            "preselection_count": self.result("preselection")["count"], "financial_success_count": len(candidates),
            "edinet_success_count": sum(r["status"] == "ok" for r in ed["results"].values()),
            "missing_data_count": sum(c.financial_data_quality_score < 100 for c in candidates),
            "yahoo_edinet_mismatch_count": sum(len(c.warnings) for c in checks.values())}
        return value, self.stats(len(candidates), len(ranking.ranked_candidates))

    def stage_report(self):
        from final_ranking import FinalCandidate, FinalRankingResult
        from final_report import FinalReportStats, write_final_report
        value = self.result("ranking")
        ranked = value["ranked_candidates"]
        snapshots = self.result("preselection")["snapshots"]
        financial, ed = self.result("financial"), self.result("edinet")
        fresh = {"JPX universe": age_freshness([self.result("universe").get("fetched_at")], self.now, 7),
            "stock_price": price_freshness([snapshots[c["code"]].get("data_as_of") for c in ranked if c["code"] in snapshots] or [c.get("source_date") for c in ranked], self.now),
            "fundamentals": age_freshness([c["fetched_at"] for c in financial["candidates"]], self.now, 7),
            "財務決算期": age_freshness([c["financial_data"].get("period_end") for c in financial["candidates"]], self.now, 180),
            "EDINET": age_freshness([r.get("data", {}).get("fetched_at") for r in ed["results"].values()], self.now, 7),
            "EDINET決算期": age_freshness([r.get("data", {}).get("period_end") for r in ed["results"].values()], self.now, 180),
            "TDnet": {"status": "unknown", "oldest": None, "newest": None, "warning": "未照合", "reason": "利用許諾未確認のためunavailable"}}
        holdings = self.result("portfolio")["holdings"]
        atomic_json(self.out / "v3_portfolio_output.json", {"generated_at": self.now.isoformat(), "holdings": holdings})
        previous = read(self.work / "previous-ranking.json", {}).get("ranked_candidates", [])
        changes = ranking_changes(ranked, previous)
        stats = self.stats(len(ranked), len(ranked))
        self.state["stages"]["report"].update(stats)
        self.state["status"] = "completed"
        self.state["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        text = build_report(self.state, ranked, holdings, fresh, changes)
        (self.out / "v3_report.md").write_text(text, encoding="utf-8")
        result = FinalRankingResult(**{k: tuple(FinalCandidate(**c) for c in value[k]) for k in ("ranked_candidates", "top_20", "top_10", "small_investment_top_10")})
        path = write_final_report(result, FinalReportStats(**value["report_stats"]), self.out / "v3_final_candidates_report.md", generated_at=self.now)
        with path.open("a", encoding="utf-8") as handle: handle.write("\n\n" + text)
        atomic_json(self.out / "v3_final_ranking.json", asdict(result))
        summary = {"freshness": fresh, "portfolio": holdings, "changes": changes,
            "top20": [c["code"] for c in ranked[:20]], "small_top10": [c["code"] for c in value["small_investment_top_10"]],
            "around_10k": [c["code"] for c in ranked if c["minimum_purchase_amount"] is not None and 5000 <= c["minimum_purchase_amount"] <= 15000],
            "category_a": [c["code"] for c in ranked if c["category"] == "A"]}
        atomic_json(self.out / "v3_pipeline_summary.json", summary)
        summary["outputs"] = {path: digest((self.out / path).read_text(encoding="utf-8")) for path in
                              ("v3_report.md", "v3_final_candidates_report.md", "v3_final_ranking.json", "v3_pipeline_summary.json", "v3_portfolio_output.json")}
        return summary, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "cached", "quick"), default="cached")
    parser.add_argument("--cache-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--new-run", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--max-operations", type=int, default=20)
    parser.add_argument("--operation-timeout", type=float, default=30)
    parser.add_argument("--worker")
    parser.add_argument("--request")
    parser.add_argument("--response")
    args = parser.parse_args()
    if args.worker:
        atomic_json(args.response, worker(args.worker, read(args.request)))
        return 0
    options = vars(args)
    for key in ("worker", "request", "response"): options.pop(key)
    try:
        state = Pipeline(**options).run()
        print(f"V3 {state['mode']}: {state['status']} elapsed={state.get('elapsed_seconds', 0)}s")
        return 0 if state["status"] == "completed" else 2 if state["status"] == "paused" else 1
    except Exception as exc:
        print(f"V3 stopped ({type(exc).__name__}); checkpoint retained", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
