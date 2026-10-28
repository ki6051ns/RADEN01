# RADEN01

Qwen を利用した Raw Data First の市場観測・ペーパートレード研究基盤（Phase 2）。
Python 3.11 以上。実売買の注文経路はありません。既定は合成データとモック判断です。

## GPU なしで実行

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/raden examples/synthetic.csv --output logs/demo.jsonl
.venv/bin/python -m raden.benchmark logs/demo.jsonl
```

出力先は新規ファイルを指定してください。既存ログは上書きしません。
同梱の4行の合成データでは40判断を実行し、101で1単位買い、104で売り、損益は3（手数料なし）になります。
MockBackend は最初の観測で entry-reviewer が buy、3番目で exit-reviewer が sell する配線確認用の固定動作です。
GPU、外部サービス、APIキーは不要です。

## Qwen3-8B / vLLM

別の Linux NVIDIA GPU 環境でモデルを1つ起動し、全銘柄・全役割が共有します。
10個のモデルをロードする構成ではありません。以下の vLLM は専用の仮想環境へインストールします。

```sh
python3 -m venv .venv-vllm
.venv-vllm/bin/pip install -r deploy/requirements-vllm.txt
export PATH="$PWD/.venv-vllm/bin:$PATH"
# モデルを取得できない環境では QWEN_MODEL_PATH に事前配備済みの重みのパスを指定
bash deploy/serve-qwen.sh
```

別ターミナルで実行します。

```sh
export QWEN_ENDPOINT=http://127.0.0.1:8000/v1/chat/completions
export QWEN_MODEL=Qwen/Qwen3-8B
.venv/bin/raden examples/synthetic.csv --backend qwen --output logs/qwen-demo.jsonl
.venv/bin/python -m raden.benchmark logs/qwen-demo.jsonl
```

Qwen3 の thinking を無効化し、JSON Schema 制約、temperature=0、出力上限512トークンを指定します。
vLLM は `0.11.0` に固定。GPUドライバー/CUDAの互換性とメモリ容量は導入先で検証してください。
モデル重み、CUDA、vLLM の実機起動はこのCPU環境では未検証です。
起動時の長さ8192は入力と出力の合計です。生データが大きい場合は `--context-events` を減らすか GPU の上限を調整してください。
文字数によるフィールド切断や特徴量への置き換えは行いません。文脈超過などの推論失敗は hold として記録します。

クラウドGPUでは認証付きTLS入口を設け、`QWEN_ENDPOINT` をHTTPS URLへ変更します。
`VLLM_API_KEY` と対応する `QWEN_API_KEY` は環境の秘密管理から注入してください。
起動スクリプトは127.0.0.1にのみバインドします。`QWEN_TENSOR_PARALLEL_SIZE` で複数GPUへのモデル分割に対応します。
将来の複数レプリカは同じAPIのロードバランサー配下へ追加できます。レプリカ配置・自動増設は未実装です。
現在のクライアントは時系列を守る逐次実行で、10判断の完了後に次イベントへ進みます。
`.env.example` は設定例で、自動読み込みはしません。

## CSV / kabuステーション参照の切り替え

`MarketDataSource` が取得元の境界です。CSVは `timestamp,symbol,price` が必須で、全列を元の文字列で保持します。
時刻はタイムゾーン必須、価格は正の有限数、入力は全体で時系列順とします。同時刻は元の行順です。
正規化、補間、リサンプリング、指標・特徴量の生成は行いません。

kabu は起動済みの kabu ステーション API と、外部で発行済みの参照用トークンを使用します。
RADEN はトークン発行・注文・取消・口座照会APIを実装していません。

```sh
# KABU_API_TOKEN は秘密管理から注入
.venv/bin/raden --source kabu --symbols 7203,6758 --polls 10 --interval 1 \
  --backend mock --output logs/kabu-paper.jsonl
```

`--backend qwen` でも同じ取得元を使用できます。
ローカルHTTPの `GET /kabusapi/board/{symbol}@1`（東証）だけを呼び出します。
クラウドからはWindows側への安全なローカルトンネルを別途用意してください。
レスポンスを `raw.board_json` に元のJSON文字列のまま保持し、受信時刻を利用可能時刻とします。
会計用に `CurrentPrice` だけを別フィールドで解釈します。将来の価格時刻、異なる銘柄、無効な価格は拒否します。
ポーリングのため更新の取りこぼしと古い価格の再観測があり、板の完全なイベント列ではありません。
実機への接続は未検証です。

## 10エージェントの入出力

役割別の指示とJSON Schemaは `src/raden/prompts.py`、プロンプト版は `phase2-v1` です。

| 役割 | 観測・判断対象 |
| --- | --- |
| market-observer | 観測された市場の流れ |
| liquidity-observer | 生の気配・板・流動性 |
| volume-observer | 出来高・取引数量 |
| price-observer | 生の価格推移 |
| entry-reviewer | 1単位のロング追加 |
| exit-reviewer | 保有ロングの解消 |
| risk-reviewer | 下振れ・買付余力 |
| execution-reviewer | 仮想約定の実行可能性 |
| counterargument-reviewer | ロング仮説への反証 |
| portfolio-reviewer | 保有と全体の仮想エクスポージャー |

各役割へ同一銘柄の現在までの生データ（既定の保持上限100件）、現在の仮想保有・現金・損益、銘柄、as_ofを渡します。
他役割の判断は渡さず、各リクエストを独立させます。必要なデータがなければ hold を指示します。
保持上限は `--context-events` で変更できます。銘柄数に比例したメモリは使用します。
出力は厳密な `{"action":"buy|sell|hold","reason":"2000文字以内"}`。
buy=1単位追加、sell=1単位解消、hold=注文なし。応答不正・出力打切り・通信失敗は hold / status=error です。

## 仮想注文・履歴・性能

10役割のbuyとsellの票数を比較し、多い側へ1単位の仮想注文を出します。同票・全holdは注文なしです。
注文は同一銘柄の**厳密に後の時刻**の次イベント価格で約定します。同一行・同一時刻では約定しません。
買付余力不足と空売りは拒否し、最終行の未約定注文はpendingのまま残します。
未来の行や他銘柄の将来価格は入力・評価に使用しません。

JSONLは `type=market` に生データ、`decision` にAI判断、`order` / `execution` / `valuation` / `summary` に仮想会計を区別します。
判断ごとに役割、モデル、プロンプト版、時刻、入力件数、推論時間ms、入力トークン数、計測元、action、reason、statusを記録します。
実モデルの入力トークン数はサーバーの `usage.prompt_tokens`。欠落・通信失敗時はnullです。
モックはUTF-8バイト数/4の概算を明示し、実トークナイザーの計測と区別します。
`raden.benchmark` は役割ごとのp50/p95、エラー数、トークン数、判断数を集計します。

実時間の推論遅延は計測のみでリプレイ時計には反映しません。
手数料、スリッページ、板の厚み、部分約定、営業日、売買単位は未モデル化です。
多数決とモックは接続確認用で、検証済みの投資戦略ではありません。
検証結果と未実装事項は [docs/phase2-validation.md](docs/phase2-validation.md) を参照してください。

## データ管理

APIキー、証券口座情報、有料市場データ、非公開フィードはコミットしないでください。
`examples/` は合成データだけです。実データは `data/raw/` または `data/private/`、判断履歴は `logs/` を利用します。
これらと `.env*` はGit除外対象です。推論先には利用許諾のあるデータだけを送信してください。
