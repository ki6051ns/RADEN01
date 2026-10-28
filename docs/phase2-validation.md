# Phase 2 検証記録

検証日: 2026-10-28。Linux / Python 3.11 / CPU環境。

## 結果

- 既存10件 + Phase 2追加8件 = 18件の unittest が成功。
- 実HTTPのローカルスタブへ40リクエストを送り、10役割の指示、JSON Schema、thinking無効化、保有状況、サーバーusage取得、仮想売買までを検証。スタブはQwen/vLLMではない。
- モックCLIの全体実行は40判断、エラー0、買い1・売り1・hold38、101で買い104で売り、実現損益3、未実現損益0、未約定0。
- 未来の入力を変更しても過去の判断入力が変わらないこと、同時刻約定禁止、銘柄別履歴、上限件数を検証。
- kabuはスタブによるGET限定、原JSON保持、将来時刻・銘柄不一致・無効価格の拒否、仮想約定接続を検証。
- 不正JSON・不正action・余分な出力フィールド・生成打切り・通信例外がholdになることを検証。通信例外の本文はログへ出さない。
- `bash -n deploy/serve-qwen.sh`、`git diff --check` 成功。

## 計測

同梱4行のCSVをモックで1回実行した40判断の計測（小標本、CPUの配線確認）。

| 項目 | 値 |
| --- | --- |
| 判断数 | 40 |
| エラー数 | 0 |
| 全判断のp50 | 0.0140 ms |
| 全判断のp95 | 0.0236 ms |
| 入力トークン概算合計 | 11,251 |
| 計測元 | mock_utf8_bytes_div4_estimate |

概算はQwenトークナイザーの値ではない。実モデルの速度・スループット・品質を示すものではない。
実際のvLLM接続時はサーバー報告の入力トークン数と、HTTP往復・待ち時間を含む判断レイテンシーを記録する。
各役割のp50/p95・エラー・トークン・action件数は次のコマンドで再計測できる。

```sh
.venv/bin/raden examples/synthetic.csv --output logs/benchmark-new.jsonl
.venv/bin/python -m raden.benchmark logs/benchmark-new.jsonl
# GPUサーバー起動・QWEN_ENDPOINT設定後
.venv/bin/raden examples/synthetic.csv --backend qwen --output logs/gpu-new.jsonl
.venv/bin/python -m raden.benchmark logs/gpu-new.jsonl
```

## 未検証・未実装

- この環境ではNVIDIA GPU・モデル重み・vLLMランタイムを利用できず、Qwen3-8B実推論、VRAM使用量、トークン毎秒、長文上限、同時負荷は未測定。起動設定の実機検証が必要。
- kabuステーション実機・Windowsトンネル・実トークンでの接続は未検証。現在は東証の有限回ポーリングのみ。PUSH接続、再接続、更新重複の排除は未実装。
- クラウドへの実配備、TLS入口、GPUレプリカ増設・オートスケール、並列リクエスト、再試行、トークンに応じた動的文脈制御は未実装。
- 市場執行の手数料・スリッページ・板数量・部分約定・推論遅延による約定時刻補正、取引所の売買単位は未実装。多数決は戦略品質を保証しない。
- 履歴は銘柄当たりの保持件数を制限するが、1行のデータサイズ・銘柄数には上限を設けていない。
- 実売買APIは実装も有効化もしていない。
