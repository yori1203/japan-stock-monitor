# V3 EDINET Validation Report

- 実行日時 JST: 2026-09-11T21:03:54+09:00
- 対象銘柄数: 15
- EDINET取得成功数: 14
- 取得失敗数: 1
- Yahoo照合成功数: 15
- matched数: 32
- warning数: 34
- データ期間不一致数: 5
- 単位補正数: 4

APIキーはレポートおよびログへ出力していません。

## 5銘柄スモーク検証

- 対象銘柄数: 5
- EDINET取得成功数: 5
- 取得失敗数: 0

## 安全な診断情報

- コードリストHTTP status: 200
- Content-Type: application/octet-stream
- CSVファイル: EdinetcodeDlInfo.csv
- 文字コード: cp932
- ヘッダー行: 2
- 証券コード対応件数: 3760
- 診断: 正常

## 8614 東洋証券株式会社

- EDINETコード: E03768
- 最新対象書類: 有価証券報告書－第104期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 100
- risk_flags: なし
- 取得できなかった項目: EDINET:revenue, EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 1.357e+10 | unavailable |
| operating_income | 2.82e+09 | 2.814e+09 | matched |
| net_income | 3.937e+09 | 3.937e+09 | matched |
| total_assets | 7.195e+10 | 7.195e+10 | matched |
| equity | 3.064e+10 | 3.064e+10 | matched |
| eps | 未取得 | 57.98 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 2317 株式会社システナ

- EDINETコード: E05283
- 最新対象書類: 有価証券報告書－第44期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 100
- risk_flags: edinet_declining_equity_ratio
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 9.44e+10 | 9.44e+10 | matched |
| operating_income | 1.537e+10 | 1.537e+10 | matched |
| net_income | 1.132e+10 | 1.131e+10 | matched |
| total_assets | 6.108e+10 | 6.108e+10 | matched |
| equity | 4.022e+10 | 3.961e+10 | matched |
| eps | 未取得 | 31.63 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 4477 ＢＡＳＥ株式会社

- EDINETコード: E35163
- 最新対象書類: 半期報告書－第14期(2026/01/01－2026/12/31)
- 対象年度/期間: 2026-01-01 - 2026-06-30
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 40
- risk_flags: revenue_mismatch, operating_income_mismatch, net_income_mismatch
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 1.239e+10 | 2.073e+10 | warning |
| operating_income | 1.068e+09 | 1.686e+09 | warning |
| net_income | 8.37e+08 | 1.826e+09 | warning |
| total_assets | 5.213e+10 | 5.78e+10 | matched |
| equity | 1.544e+10 | 1.492e+10 | matched |
| eps | 未取得 | 18.88 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 3679 株式会社じげん

- EDINETコード: E30047
- 最新対象書類: 有価証券報告書－第20期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 66.67
- risk_flags: net_income_mismatch, equity_mismatch
- 取得できなかった項目: EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 2.922e+10 | 2.922e+10 | matched |
| operating_income | 5.913e+09 | 5.913e+09 | matched |
| net_income | 2.148e+09 | 4.157e+09 | warning |
| total_assets | 4.056e+10 | 4.056e+10 | matched |
| equity | 1.052e+10 | 2.251e+10 | warning |
| eps | 41.66 | 41.62 | matched |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 2492 株式会社インフォマート

- EDINETコード: E05609
- 最新対象書類: 半期報告書－第29期(2026/01/01－2026/12/31)
- 対象年度/期間: 2026-01-01 - 2026-06-30
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 0
- risk_flags: revenue_mismatch, operating_income_mismatch, net_income_mismatch, equity_mismatch, total_assets_mismatch
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 9.99e+09 | 1.882e+10 | warning |
| operating_income | 2.119e+09 | 2.864e+09 | warning |
| net_income | 1.173e+09 | 1.923e+09 | warning |
| total_assets | 3.386e+10 | 1.817e+10 | warning |
| equity | 3.01e+10 | 1.214e+10 | warning |
| eps | 未取得 | 9.35 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 8628 松井証券株式会社

- EDINETコード: E03807
- 最新対象書類: 有価証券報告書－第110期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 100
- risk_flags: なし
- 取得できなかった項目: EDINET:revenue, EDINET:eps, EDINET:shares_outstanding, Yahoo:operating_income

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 4.914e+10 | unavailable |
| operating_income | 2.346e+10 | 未取得 | unavailable |
| net_income | 1.548e+10 | 1.548e+10 | matched |
| total_assets | 1.354e+12 | 1.354e+12 | matched |
| equity | 8.235e+10 | 8.235e+10 | matched |
| eps | 未取得 | 59.99 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 3660 ウェルネス・コミュニケーションズ株式会社

- EDINETコード: E37743
- 最新対象書類: 有価証券報告書－第20期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 0
- risk_flags: revenue_mismatch, operating_income_mismatch, net_income_mismatch, equity_mismatch, total_assets_mismatch
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 1.478e+10 | 6.877e+10 | warning |
| operating_income | 1.186e+09 | 3.164e+09 | warning |
| net_income | 8.221e+08 | 2.327e+09 | warning |
| total_assets | 9.729e+09 | 3.46e+10 | warning |
| equity | 5.833e+09 | 1.686e+10 | warning |
| eps | 未取得 | 30.1 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 7803 株式会社ブシロード

- EDINETコード: E35004
- 最新対象書類: 未取得
- 対象年度/期間: 未取得
- EDINET/Yahoo状態: no_recent_filing / ok
- EDINET失敗理由: useful XBRL document not found within 190 days (edinet_code=E35004)
- crosscheck_score: 未取得
- risk_flags: なし
- 取得できなかった項目: EDINET:revenue, EDINET:operating_income, EDINET:net_income, EDINET:total_assets, EDINET:equity, EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 5.618e+10 | unavailable |
| operating_income | 未取得 | 4.868e+09 | unavailable |
| net_income | 未取得 | 3.418e+09 | unavailable |
| total_assets | 未取得 | 4.98e+10 | unavailable |
| equity | 未取得 | 2.419e+10 | unavailable |
| eps | 未取得 | 33.26 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 7354 株式会社ダイレクトマーケティングミックス

- EDINETコード: E35931
- 最新対象書類: 半期報告書－第10期(2026/01/01－2026/12/31)
- 対象年度/期間: 2026-01-01 - 2026-06-30
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 25
- risk_flags: revenue_mismatch, operating_income_mismatch, eps_mismatch
- 取得できなかった項目: EDINET:net_income, EDINET:equity, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 1.146e+10 | 2.269e+10 | warning |
| operating_income | 1.363e+09 | 2.133e+09 | warning |
| net_income | 未取得 | 1.345e+09 | unavailable |
| total_assets | 2.69e+10 | 2.742e+10 | matched |
| equity | 未取得 | 1.488e+10 | unavailable |
| eps | 27.22 | 38.71 | warning |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 8789 フィンテック　グローバル株式会社

- EDINETコード: E05492
- 最新対象書類: 半期報告書－第32期(2025/10/01－2026/03/31)
- 対象年度/期間: 2025-10-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 20
- risk_flags: revenue_mismatch, operating_income_mismatch, net_income_mismatch, equity_mismatch
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 8.011e+09 | 1.443e+10 | warning |
| operating_income | 2.553e+09 | 3.406e+09 | warning |
| net_income | 3.206e+09 | 2.122e+09 | warning |
| total_assets | 2.696e+10 | 2.699e+10 | matched |
| equity | 1.457e+10 | 1.097e+10 | warning |
| eps | 未取得 | 20.77 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 7203 トヨタ自動車株式会社

- EDINETコード: E02144
- 最新対象書類: 有価証券報告書－第122期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 40
- risk_flags: revenue_mismatch, net_income_mismatch, equity_mismatch
- 取得できなかった項目: EDINET:eps, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 1.826e+13 | 5.068e+13 | warning |
| operating_income | 3.766e+12 | 3.766e+12 | matched |
| net_income | 3.392e+12 | 3.848e+12 | warning |
| total_assets | 1.055e+14 | 1.055e+14 | matched |
| equity | 2.366e+13 | 3.992e+13 | warning |
| eps | 未取得 | 351.5 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 8306 株式会社三菱ＵＦＪフィナンシャル・グループ

- EDINETコード: E03606
- 最新対象書類: 有価証券報告書－第21期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 100
- risk_flags: なし
- 取得できなかった項目: EDINET:revenue, EDINET:eps, EDINET:shares_outstanding, Yahoo:operating_income

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 7.521e+12 | unavailable |
| operating_income | 1.4e+12 | 未取得 | unavailable |
| net_income | 2.561e+12 | 2.427e+12 | matched |
| total_assets | 4.317e+14 | 4.317e+14 | matched |
| equity | 2.374e+13 | 2.227e+13 | matched |
| eps | 未取得 | 236.6 | unavailable |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 6758 ソニーグループ株式会社

- EDINETコード: E01777
- 最新対象書類: 有価証券報告書－第109期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 33.33
- risk_flags: revenue_mismatch, net_income_mismatch, equity_mismatch, eps_mismatch
- 取得できなかった項目: EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 1.645e+11 | 1.248e+13 | warning |
| operating_income | 1.448e+12 | 1.554e+12 | matched |
| net_income | 4.623e+11 | -3.269e+11 | warning |
| total_assets | 1.568e+13 | 1.568e+13 | matched |
| equity | 2.755e+12 | 8.119e+12 | warning |
| eps | -54.7 | 186.4 | warning |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 9432 ＮＴＴ株式会社

- EDINETコード: E04430
- 最新対象書類: 有価証券報告書－第41期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 60
- risk_flags: net_income_mismatch, equity_mismatch
- 取得できなかった項目: EDINET:revenue, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 1.441e+13 | unavailable |
| operating_income | 1.706e+12 | 1.786e+12 | matched |
| net_income | 9.219e+11 | 1.037e+12 | warning |
| total_assets | 4.672e+13 | 4.672e+13 | matched |
| equity | 6.886e+12 | 9.728e+12 | warning |
| eps | 12.61 | 12.61 | matched |
| shares_outstanding | 未取得 | 未取得 | unavailable |

## 9984 ソフトバンクグループ株式会社

- EDINETコード: E02778
- 最新対象書類: 有価証券報告書－第46期(2025/04/01－2026/03/31)
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
- EDINET失敗理由: なし
- crosscheck_score: 40
- risk_flags: operating_income_mismatch, net_income_mismatch, equity_mismatch
- 取得できなかった項目: EDINET:revenue, EDINET:shares_outstanding

| 項目 | EDINET | Yahoo | 判定 |
|---|---:|---:|---|
| revenue | 未取得 | 7.799e+12 | unavailable |
| operating_income | 1.953e+12 | -4.789e+09 | warning |
| net_income | 1.492e+12 | 5.002e+12 | warning |
| total_assets | 6.075e+13 | 6.075e+13 | matched |
| equity | 6.818e+12 | 1.762e+13 | warning |
| eps | 873.5 | 872.4 | matched |
| shares_outstanding | 未取得 | 未取得 | unavailable |
