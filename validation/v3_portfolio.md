# V3保有株・営業日判定の検証

## 9月11日再開時の最小修正

- 前回15ファイルはリモート4352226へ反映済み。ローカルとのGit tree完全一致を確認して継続。
- 将来保有株がpreselection上位へ入る場合も新規候補から除外する防御をランキング入口へ追加。portfolio工程の分析は維持。
- 保有継続・買い増し・利確警戒・損切り警戒・様子見・縮小検討の理由を併記。
- EDINETのAPIエラー本文・異常なstatusにsecretが含まれても例外へ転記しない。
- 227テスト成功、失敗0（13.45秒）。既存224件と追加3件。営業日判定も外部通信なしで検証。
- cached実測1.922秒、4銘柄出力、候補50銘柄順位変化なし。Yahoo/JPX/EDINET/TDnet再取得なし。
- V2本体、朝昼夕レポート、既存workflow、mainは変更なし。
- リモートmain a0277b9のEDINET workflowはtarget_ref入力とcheckout refを維持。実取得ジョブは今回起動していない。
- 保有株評価は下表と同値。9月7日の日足キャッシュはstale、confidence=low、取得単価なしの暫定評価。

以下は前回の完成時点の記録。

- 既存196件＋追加28件: `pytest -q` 224 passed / 11.11秒。
- 最終cached実測: 1.718秒、4銘柄全件出力、候補50銘柄の順位変化なし。
- 今回の再開時にはYahoo/JPX/EDINET/TDnetへのデータ取得を行っていない。
- 財務とテクニカルは9月7日の保存結果を再利用。日足の基準日は9月7日であり、現在の最新値ではない。

## 保有株専用分析

config.jsonを読み取り専用で利用。V3専用設定v3_portfolio_settings.jsonから会社名・average_costを補完する。
null設定は既存の取得単価を消さない。保有株はpreselection順位に関係なく独立したportfolio工程で評価し、候補ランキングへ挿入しない。
CIでも取得済み4銘柄を使えるようvalidation/v3_portfolio_input.jsonに市場・財務データを保存した。

| コード | portfolio_score | 暫定action |
|---|---:|---|
|6740|35.54|stop_loss_watch|
|6573|48.72|hold|
|4596|38.18|reduce|
|4597|28.28|reduce|

配点はfinancial25、technical25、momentum15、EDINET15、risk10、event10。
今回のEDINETとTDnetはunavailableのため除外し、取得済み75点分の配点で再正規化した。
riskは観測した財務・材料リスクへの減点であり、未取得リスクがないことを保証しない。
scoreの内訳、取得可能/不足項目、評価理由、取得日時・鮮度をJSONおよび両レポートに出す。
平均取得単価なしでも評価し、損益・厳密な利確損切り価格だけ算出不可とする。
日足が古い場合はconfidenceをlowへ下げる。自動売買は行わない。

## 更新と再開

`python v3_pipeline.py --mode cached --new-run` はネットワークを使わず再評価する。
完了したcheckpointをそのまま再利用する実行では、日時を更新しない。
`python v3_portfolio.py --refresh` は保有株だけの欠損/期限切れ財務・テクニカルを更新する独立した明示操作。
各取得は45秒上限、2秒間隔、項目ごと保存、失敗から1時間は再試行しない。
通常のfull候補取得とは別の入口であり、保有株更新のために全銘柄を再取得しない。

## オフライン営業日カレンダー

JPX公式の2026・2027年休業日（土日・祝日・振替休日・9月22日・年末年始）を同梱。
15:30 JST以降は当営業日の終値、引け前は前営業日の終値を期待する。
日曜の金曜終値、月曜寄り前、祝日翌寄り前はfresh。必要終値に足りなければstaleと理由を表示。
当日引け前の速報値はacceptable。未知の日付・対象年外はunknown（推定カレンダーで代用しない）。
2028年以降および臨時休場日は公式発表を確認して更新が必要。

参照: https://www.jpx.co.jp/corporate/about-jpx/calendar/
立会時間: https://www.jpx.co.jp/equities/trading/domestic/01.html

## workflow確認

リモートmain d854a86上のV3 EDINET Validationはworkflow_dispatchとtarget_ref入力を持つ。
GitHub画面でUse workflow from: main、target_ref: feature/v3-discovery-engine、Run workflowボタンを確認した。
main側のcheckoutはinputs.target_refを利用する。mainおよびEDINET workflowは変更していない。
今回は実行可否の確認までとし、新しいEDINET実取得ジョブを開始していない。Secret値も参照・出力していない。
このworkflowはEDINET実検証用で、V3統合cached workflowとは別である。
V3統合workflowのartifactにはJST日時、portfolio出力を追加。scheduleはコメントのまま。
cached/quickは新しい評価を開始し保存データを再利用。fullの途中再開ではnew_run=falseを使用する。

残課題: 保有株EDINET公式照合、TDnet許諾、保存株価の明示更新、V3統合workflowの実運用検証。
