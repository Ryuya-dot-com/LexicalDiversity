# Synthetic pilot protocol

この文書は、公開 LLM 生成作文ベンチマークの48作文pilotを実行する前に固定する契約である。機械可読な正本は [`benchmarks/synthetic/pilot-protocol.json`](../benchmarks/synthetic/pilot-protocol.json) であり、この仕様作成ではAPIを呼び出しておらず、費用も発生していない。現時点の状態は `specified_not_executed`、`generation_authorized=false` である。

## 目的と解釈境界

pilotの目的は、生成API、長さ遵守、拒否、provenance、再試行、QC、重複検査、費用停止を小規模に検証することにある。register条件は、語彙指標が明示的な文体操作へどう応答するかを調べる実験条件であり、文章品質や人間の能力を表す正解ラベルではない。

CEFR水準の指定・予測、人間のL2執筆者の擬態、意図的な学習者風誤り、熟達度または作文品質のgold label、AI著者判定は、prompt・分析・公開時のいずれでも禁止する。pilotの結果から主張できるのは、記述的指標の応答性、安定性、感度に限る。

## 固定する48条件

pilotは次の完全要因計画であり、1つの日付固定model snapshotを使って各セルを1回だけ生成する。

| 要因 | 水準 | 数 |
| --- | --- | ---: |
| topic | 公共図書館、都市緑地、公共交通、食品廃棄など、JSONに固定した一般的な12題 | 12 |
| genre | `argumentative`, `expository` | 2 |
| register | `plain`, `formal_academic` | 2 |
| model snapshot | preflightで固定する1 snapshot | 1 |
| replicate | 各セル1回 | 1 |

したがって、`12 × 2 × 2 × 1 × 1 = 48` 作文である。core v1で用いる `neutral` register、2 model snapshots、3 replicatesはpilotには入れない。pilot作文を後から黙ってcoreへ流用することもない。

各promptは、JSONに記録されたsystem prompt、topic別task、genre要件、register要件から決定論的に組み立てる。目標は約250語、QC範囲は225–275語とする。範囲外、拒否、異常終了も削除または「より良い文章」への生成し直しをせず、flag付きの観測結果として残す。100・150・200 tokenの接頭部分は、公開済み正規化文から固定tokenizerで後から派生させるため、追加API費用は発生しない。

## 生成14日前のpreflight

モデルはこの文書の作成時点では選定していない。生成を許可するには、最初のrequestより少なくとも14日前に、providerが管理する公式ページだけを用いて、次を1つのfrozen recordへ固定する。

- provider、要求する正確なmodel snapshot ID、期待するresponse model ID
- 公式model documentation、価格、termsのURLと取得UTC時刻
- input、output、cached input、その他課金tokenの単価と課金通貨
- 税の扱い、必要な場合は円換算の出典・rate
- 価格証拠とmodel capability証拠のSHA-256
- 最初に生成可能となるUTC時刻

実行開始時に公式ページを再確認し、snapshot、価格、terms、対応parameterのいずれかが変わっていれば停止する。新しいprotocol versionと理由を作り、14日間の時計を最初からやり直す。model IDの暗黙のalias移動は認めない。

ほかにも、48セルscheduleと全prompt hash、最悪費用見積、秘密情報境界、generator commit・lockfile・QC code、offline stop simulation、公開条件の全gateが `pass` になるまで `generation_authorized` をtrueにしてはならない。

## 費用、停止、再試行

pilotの自動停止額は6,000円、プロジェクト全体の絶対上限は10,000円である。残り4,000円はrunning pilotが自動利用できる予備費ではない。各requestの直前に、確定費用、未解決の予約額、次のattemptの最悪費用を合算し、pilotが6,000円未満かつ全体が10,000円未満にとどまる場合だけ送信できる。予測額が上限と一致するrequestも送らず、確定額または予約額が上限以上なら停止する。失敗・再試行分もすべて課金額へ含める。usageまたは単価が不明、あるいはprovider明細と計算が食い違う場合は、高い方を費用として予約し、解決まで次を送らない。

1セルにつき最大3 attempts、すなわち最大2 retriesとする。retry対象は、request受理前と確認できる接続障害、`Retry-After`を伴うrate limit、完成responseのないprovider 5xxだけである。backoffは2秒、8秒でjitterは使わず、prompt・parameterは変えない。認証失敗、delivery状態不明のtimeout、model/schema/price drift、拒否、語数逸脱、重複、異常finish reasonはretryしない。QCを見てbest-of-nを選ぶことも禁止する。

予算到達、preflight失効、model不一致、費用計算不能、secret混入、response schema drift、二重課金の恐れがあるdelivery不明は即時に全runを止める。また、transport terminal failureが3セル連続するか、10セル以上の時点でterminal failure率が20%以上になれば運用停止する。再開には新しいrun IDと理由が必要で、累積費用をゼロへ戻してはならない。

## QCと重複検査

語数はNFC正規化後、`[A-Za-z]+(?:['’][A-Za-z]+)?` に一致する単位を数える。各作文には、空文、finish reason、拒否、語数範囲、title/list wrapper、prompt反復、完全重複、近似重複、secretらしい文字列、個人情報確認のflagを必ず持たせる。

完全重複は、NFC・小文字化・改行統一・空白圧縮後のUTF-8文字列のSHA-256で判定する。近似重複は48作文全組合せについて、小文字word-token 5-gram集合のJaccard係数を計算し、`0.85` 以上をflagする。重複flagは再生成や削除の指示ではなく、観測されたAPI挙動である。語彙指標値をQCやcore移行判断へ使ってはならない。

## 完全provenance

API attemptごとにimmutable JSONL recordを1行、予定セルごとにdocument recordを1行作る。最低限、次を保存する。

- document・condition・attempt・retryのIDとschedule位置
- 完全なsystem/user prompt、そのSHA-256、全generation settings、seed対応状況
- provider、要求model、response model、endpoint、UTC時刻、request ID、SDK版
- sanitize済みrequest/response JSON body、status、finish reason、拒否情報
- input/output/cached等のtoken使用量、固定単価record hash、attempt費用・累積費用
- raw/normalized textと各SHA-256、語数、QC・duplicate flags
- protocol、generator script、Git commit、lockfile、分析tool、resourceのhash

HTTP transport headerは保存しない。保存対象は正のfield allowlistで組み立て、request/response bodyは書込み前にsanitizeする。hidden reasoningは要求も保存もしない。

## 公開境界と秘密情報

現段階でGit追跡と公開を許可するSynthetic benchmarkメタデータは `benchmarks/synthetic/pilot-protocol.json` だけである。生成作文、request/response、JSONL、manifest、QC出力は、将来の権利・秘密情報・内容審査とrelease gateの明示的なallowlist更新が完了するまで、名前や拡張子を変えても追跡してはならない。

将来の審査後に公開する意向があるのは、固定protocolと改訂履歴、完全prompt、生成作文、sanitize済みrequest/response body、model・設定・日時・request ID・token・費用、全QC flag、software/resource identity、manifest、data card、集計QC reportである。ただし、provider termsとdataset licenseをpreflightで確認するまでは公開状態を `blocked` とする。

API key、access token、authorization header、cookie、signed URL、credential file、secret-manager内部識別子、環境変数dump、billing account、非公開organization情報、絶対local path、username、hostname、IP、transport debug trace、人間作文、private corpus excerptは公開しない。認証情報は承認済みsecret channelからだけ供給し、CLI argumentやprotocolへ書かない。各書込み前とrelease staging時にsecret scanを実行し、検出時はrunを停止してlocal quarantineし、公開しない。

## 二種類の再現性

**分析再現性はrelease要件として保証する。** 公開した正規化文、そのhash、分析tool commit、lockfile、resource manifest/hash、分析設定から同じ指標を再計算できなければreleaseしない。

**生成requestの再送はbest effort、文字列の完全再生成は保証しない。** 完全prompt、model snapshot、settings、seed対応、時系列を公開しても、hosted modelの配信基盤、安全層、backendは変化し得る。将来再送した文字列ではなく、hash付きで公開した元の生成文を分析のcanonical inputとする。

## Pilot exit gate

core生成へ進めるのは、48セルすべてにsilent replacementのないterminal recordがあり、provenance schemaが完全で、費用上限を一度も越えず、停止simulationが通り、長さ・拒否・失敗・重複率を報告し、release stagingに秘密情報や未解決の個人情報flagがなく、clean environmentで公開文から分析を再現できた場合だけである。不都合な出力や失敗もpilotの証拠であり、削除してはならない。
