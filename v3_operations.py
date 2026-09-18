"""Session orchestration only; existing monitoring and V3 scoring stay unchanged."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from datetime import datetime
from pathlib import Path

import backtest
import monitor
from history import FIELDS, _atomic_write, _read_rows, migrate_history
from v3_pipeline import Pipeline

EXPECTED = {"portfolio": {"6740", "6573", "4596", "4597", "6721"},
            "watchlist": {"6177", "2134", "2410", "4583"}}


def save_signals(path, rows):
    """One record per JST date/session/code, including category overlaps."""
    migrate_history(path)
    unique = {}
    for row in _read_rows(Path(path)) + rows:
        key = (str(row["date"])[:10], row["session"], row["code"])
        unique.setdefault(key, {field: row.get(field, "") for field in FIELDS})
    _atomic_write(Path(path), list(unique.values()))


def full_run(root, output):
    state = {}
    # Each call retains the existing bounded full/resume behavior and caches.
    for chunk in range(60):
        state = Pipeline(root=root, output_dir=output, mode="full", new_run=chunk == 0,
                         max_seconds=180, max_operations=20).run()
        if state["status"] != "paused":
            break
        time.sleep(15)
    return state


def run(session, root, output, *, now=None, pipeline_runner=full_run):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(monitor.JST)
    config = monitor.load_config(str(root / "config.json"))
    stocks = monitor.configured_stocks(config)
    issues, results, candidates, technical = [], [], [], []
    actual = {category: {s["code"] for s in stocks if s["category"] == category}
              for category in EXPECTED}
    if actual != EXPECTED or len(stocks) != 9:
        issues.append({"stage": "configuration", "status": "system_error", "reason": "required_universe_mismatch"})
    # Never stop processing the remaining required symbols after one failure.
    for stock in stocks:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = monitor.analyse(stock["code"], stock["category"], stock["priority"])
            results.append(result)
        except Exception as exc:
            issues.append({"stage": "Yahoo", "code": stock["code"], "status": "system_error",
                           "reason": type(exc).__name__})
    state = {}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            state = pipeline_runner(root, output)
        if state.get("status") != "completed":
            raise RuntimeError("incomplete_pipeline")
        candidates = json.loads((output / "v3_final_ranking.json").read_text(encoding="utf-8"))["ranked_candidates"][:5]
        if len(candidates) != 5 or len({c["code"] for c in candidates}) != 5:
            raise ValueError("top5_incomplete")
        for name, stage in state.get("stages", {}).items():
            if stage.get("failure_count", 0):
                issues.append({"stage": name, "status": "system_error", "reason": "acquisition_failure",
                               "count": stage["failure_count"]})
        if state.get("edinet_counts", {}).get("unavailable", 0):
            issues.append({"stage": "EDINET", "status": "system_error", "reason": "acquisition_unavailable"})
        for candidate in candidates:
            existing = next((r for r in results if r["code"] == candidate["code"]), None)
            if existing:
                technical.append(existing)
                continue
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    technical.append(monitor.analyse(candidate["code"], "discovery", "normal"))
            except Exception as exc:
                issues.append({"stage": "Yahoo", "code": candidate["code"], "status": "system_error",
                               "reason": type(exc).__name__})
    except Exception as exc:
        issues.append({"stage": "V3", "status": "system_error", "reason": type(exc).__name__})
    bt, bt_errors = [], []
    for stock in stocks:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                market = backtest.download_stock(stock["code"], period="2y", auto_adjust=True)
                bt.append(backtest.summarize(stock["code"], backtest.simulate(market.frame, 10)))
        except Exception as exc:
            bt_errors.append(f"{stock['code']}: {type(exc).__name__}")
    backtest.create_report(bt, bt_errors, output / "backtest_report.md", "2y", 10)
    if bt_errors:
        issues.append({"stage": "backtest", "status": "system_error", "reason": "acquisition_failure", "count": len(bt_errors)})
    complete = (not issues and len(results) == 9 and len(candidates) == 5 and len(technical) == 5)
    path = output / f"report_{session}.md"
    monitor.create_report(results, [], [], generated_at=now, session=session, output=path)
    text = path.read_text(encoding="utf-8")
    text += f"\n\n## V3運用セッション\n\n- session: {session}\n- 正常完了: {'YES' if complete else 'NO'}\n"
    text += "- 自動探索は既存V3順位の上位5件を使用。日足監視スコアとV3採点は別表示。\n"
    text += "- 比較不能・正常欠損は一致・加点ではありません。取得障害とは区別します。\n"
    text += "\n## V3自動探索 上位5候補\n\n"
    for candidate in candidates:
        quote = next((r for r in technical if r["code"] == candidate["code"]), {})
        text += f"- {candidate['rank']}. {candidate['code']} {candidate.get('company_name', '')}: V3 {candidate['final_score']}; 日足データ基準日 {quote.get('data_as_of', '取得失敗')}; EDINET {candidate.get('edinet_status', 'unavailable')}\n"
    text += "\n## 比較条件・取得状態\n\n"
    text += f"- EDINET取得状態: {json.dumps(state.get('edinet_counts', {}), ensure_ascii=False)}\n"
    text += f"- 比較判定: {json.dumps(state.get('crosscheck_counts', {}), ensure_ascii=False)}\n"
    text += "- no_recent_filing: 対象期間内に提出なし（正常欠損）。not_comparable: 比較条件未成立。いずれもシステム障害ではありません。\n"
    text += "\n## 運用障害\n\n" + ("なし\n" if not issues else "\n".join(json.dumps(e, ensure_ascii=False) for e in issues))
    path.write_text(text, encoding="utf-8")
    (output / "report.md").write_text(text, encoding="utf-8")
    rows = [{**r, "date": now.strftime("%Y-%m-%d %H:%M"), "session": session,
             "run_id": f"{now:%Y%m%d}-{session}"} for r in results + technical]
    save_signals(output / "signals.csv", rows)
    status = {"session": session, "started_at": now.isoformat(), "completed_at": datetime.now(monitor.JST).isoformat(),
              "complete": complete, "expected": {k: sorted(v) for k, v in EXPECTED.items()},
              "processed": [r["code"] for r in results], "top5": [c["code"] for c in candidates], "issues": issues,
              "edinet_counts": state.get("edinet_counts", {}), "crosscheck_counts": state.get("crosscheck_counts", {})}
    (output / "operations_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if complete else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, choices=["morning", "noon", "evening"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.session, Path(__file__).resolve().parent, args.output))
