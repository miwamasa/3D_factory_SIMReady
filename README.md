# Motor Factory Lab — SCADA Console

モーター組立6工程を題材とする、AIエージェント開発用のローカルテストベンチです。
Python 3.9以上とブラウザだけで動作します。追加パッケージは不要です。

工場構成、キーの使い分け、通常運転と異常デモの手順は[操作・デモガイド](doc/guide_ja.md)を参照してください。
「工場 3D」ではBlenderモデルを使った組立動作を接写し、工程デモで繰り返し観察できます。生産指標や時間ジャンプも利用できます。[3Dビューの操作ガイド](doc/factory3d.md)

## 起動

```sh
cd motor_factory_lab
python3 server.py --demo
```

http://127.0.0.1:8765 を開いてください。起動中なら画面を再読み込みするだけでUIの変更を反映できます。
停止する場合はサーバーを起動したターミナルでCtrl+Cを押してください。

`scada_preview.html` はファイル単独で開ける画面確認用です。正常・工具ドリフト・軸受過熱・欠品を切り替えられます。
プレビューは固定スナップショットの表示だけを行い、APIや認証・承認には接続しません。

## SCADA風の画面

- 暗色の操作盤、機器タグ、設備ミミック、搬送コンベヤ、状態ランプ。
- 緑は加工中、黄は搬送待ち・工具ドリフト、赤は設備故障。状態名も併記。
- 設備をクリックすると温度・サイクル時間・仕掛品・シリアル・累積処理数の詳細を表示。
- 上部に警報帯、下部に生産トレンド、アラームサマリ、ERP製造指図を表示。
- 通信断時はSTALE DATA / COMMS LOSTを表示し、最終受信値であることを明示。
- OEEは起動過渡を含む学習用の推定値。実設備の性能計測・安全制御には使用しません。

## 人の認証と承認

起動後、別ターミナルで次のコマンドを実行し、表示されたキーを「人のキーで接続」に入力します。

```sh
python3 credentials.py supervisor
python3 credentials.py planner
```

supervisorは現場責任者、plannerは生産計画担当者です。人のキーは画面のメモリにのみ保持します。
`--demo`ではエージェント用のキーだけをローカル画面へ自動配布します。人のキーは自動配布しません。
デモを無効にして起動した場合は、エージェントのキーも明示的に入力してください。

```sh
python3 credentials.py agent
```

開発用資格情報は`.runtime/credentials.json`、工場状態・承認・実行記録は`.runtime/factory.sqlite`に保持します。
実際の本人確認・MFA・OAuth/OIDCは未実装です。後でIdPによる認証へ差し替えるための境界を持つ試験実装です。
同じOSアカウントで実行する任意のプログラムから人のキーを読める開発環境なので、悪意あるローカルエージェントとの隔離には使えません。

## 承認フロー

1. `factory_preflight`で実行主体・対象・引数・状態バージョンを固定した操作案を作成。
2. 承認が必要な場合、人が別資格情報と判断理由を用いて`POST /api/approval`で承認・却下。
3. 提案した主体が`factory_execute`へ操作案IDと冪等性キーを渡して実行。

有効期限は実時間で5分です。承認者ロール、提案者との分離、設備・指図の変更、許可範囲を実行時に再検証します。
承認済みというクライアントの自己申告は受け付けません。引数は操作案から取得するので実行時に差し替えられません。
状態更新・承認の消費・実行記録を一つのSQLiteトランザクションで確定します。
同じ冪等性キーでの再試行は同じ結果を返し、別のキーでの承認再利用は拒否します。

通信はstatelessですが、工場状態・操作案・承認・監査記録はサーバー側に保持します。

|操作|階層|承認|
|---|---|---|
|スナップショット・指図・在庫の参照|L3/L4|不要|
|ラインの一時停止|L3|不要|
|ラインの再開|L3|現場責任者|
|レシピ変更・保全|L3|現場責任者。ライン停止が前提|
|優先度変更・指図リリース・補充|L4|生産計画担当者|
|PLC直接書込み・不良品の判定上書き|L1/L2等|公開せず拒否|

## エージェント接続

RESTの入口は`POST /api/tool`です。毎回`Authorization: Bearer <agent key>`を渡します。

```json
{"name":"factory_preflight","arguments":{"action":"mes.pause_line","target":"LINE-01","arguments":{}}}
```

```json
{"name":"factory_execute","arguments":{"proposal_id":"<返された操作案ID>","idempotency_key":"trial-0001"}}
```

公開ツール: `mes_get_snapshot`, `erp_get_orders`, `erp_get_inventory`, `factory_get_policy`,
`factory_get_audit`, `factory_list_proposals`, `factory_preflight`, `factory_execute`。

MCP HTTPは`POST /mcp`。2026-07-28のtoolsを中心とした小規模プロファイルで、
`server/discover`, `ping`, `tools/list`, `tools/call`に対応します。
`MCP-Protocol-Version`, `Mcp-Method`と、tools/callの場合は`Mcp-Name`も指定します。
`Accept: application/json, text/event-stream`を送り、paramsの`_meta`にもprotocolVersionとclientCapabilitiesを含めます。
OAuth・MRTR・subscriptions・外部MCPクライアントでの網羅的な適合性試験は対象外です。

旧クライアントではstdioアダプタを利用できます。
アダプタの環境変数`FACTORY_AGENT_TOKEN`へエージェントのキーを渡して`python3 stdio_bridge.py`を起動します。
接続先は`FACTORY_URL`（既定値`http://127.0.0.1:8765`）です。
アダプタは2025-11-25のinitializeにも応答し、HTTP側へ転送します。

「次の一手」は実際の状態を参照するルールベース見本です。LLMは未接続です。
モデルを接続する際はエージェントに人のキーを渡さず、同じ公開ツールを呼び出してください。

## シミュレーションと検証

部材供給 → ステータ巻線 → 回転子・軸受 → ハウジング組付 → ボルト締付 → EOL検査。
各工程のバッファ上限は3台。乱数seedを固定して再現できます。製品のトルク検査基準は4.2±0.35 N·mで固定です。
工具ドリフトは+0.9 N·m。軸受過熱は設備インターロック停止。補充は仮想時間60秒後に入荷します。
保全作業は簡易化し、承認後の実行で即時に異常を除去します。保全後もラインは停止を維持します。

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

既存の22件のバックエンド・HTTP・MCP・stdioテストを再実行して成功しています。
SCADA版のブラウザ自動操作・スクリーンショットによる最終確認は、今回の接続制約のため未実施です。
