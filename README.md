# Japan Stock Monitor v2

日本株の日足データを朝夕に取得し、保有銘柄・監視銘柄・自動探索候補を同一の判定ロジックで評価する監視バッチです。表示価格はリアルタイム価格ではありません。

## 実行

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python monitor.py --session morning
python monitor.py --session evening
python backtest.py
```

朝夕のレポートは `report_morning.md` と `report_evening.md` に保存され、`report.md` は最後に生成したレポートと同期します。履歴は `signals.csv`、検証結果は `backtest_report.md` に保存されます。

## 履歴の互換性

旧版の7列形式 `signals.csv` は初回実行時に拡張形式へ自動移行されます。旧列と全行は保持され、移行前の内容は `signals.csv.v1.bak` に一度だけバックアップされます。同じ日・同じセッション・同じ銘柄の再実行は重複追加されません。

## 自動探索

`config.json` の `auto_discovery.candidate_codes` を共通ロジックで採点し、設定銘柄を除いた上位候補をレポートします。候補集合は明示管理するため、外部サイトの構造変更で対象銘柄が意図せず変わりません。

## 注意

- Yahoo Financeの日足取得には遅延や欠損が生じることがあります。
- レポートにはデータ取得日時（JST）とデータ基準日を別々に表示します。
- 本システムは監視・検証用であり、投資助言や自動売買システムではありません。
