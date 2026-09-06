# TDnet mock ranking comparison

仮想イベントです。実IRではありません。同じ新配点のTDnet未照合ランキングとの比較です。

未照合時は90点分で再正規化、取得済み中立時はTDnet=50を含む100点分で計算します。増配などの好材料でも未照合時より総合点が低くなる場合があります。neutral deltaは同じ取得状態での材料効果です。

配点変更前のEDINET版からの変動と混同しないよう、baselineも新配点で計算しています。

| code | event | baseline rank | mock rank | baseline score | mock score | delta | neutral delta |
|---|---|---:|---:|---:|---:|---:|---:|
| 2146 | upward_revision | 3 | 1 | 80.63 | 89.45 | +8.82 | +11.88 |
| 8616 | share_buyback | 5 | 2 | 77.67 | 83.90 | +6.23 | +9.00 |
| 2317 | dividend_increase | 2 | 3 | 81.87 | 80.56 | -1.31 | +1.88 |
| 4406 | capital_alliance | 6 | 4 | 77.64 | 76.00 | -1.64 | +1.12 |
| 8410 | share_issuance | 7 | 7 | 77.36 | 73.12 | -4.24 | -1.50 |
| 8614 | warrant | 1 | 25 | 82.77 | 64.87 | -17.90 | -14.63 |
| 8572 | downward_revision | 4 | 45 | 78.50 | 57.13 | -21.37 | -18.52 |
