# 戦略ロードマップ 2026–2028（公開ツール・ファースト版）

初版: 2026-07-22
方針更新: 2026-07-24

本書は [`roadmap.md`](roadmap.md) のフェーズゲートを置き換えず、限られた時間と
資金を何に先に使うかを決める上位の順序付け層である。2026-07-23 の方針確認により、
最優先成果を査読論文から**公開ツール**へ変更した。論文は公開・検証済みツールから
生じる成果であり、ツール公開を遅らせる独立目的にはしない。

---

## 0. 確定した制約と北極星

- **第一目的:** 誰でも入手でき、同じ入力・設定・資源版から同じ結果を再計算できる
  英語語彙プロファイリングツールを継続公開する。
- **研究体制:** 単独研究。新規の人間 L2 作文収集は行わない。
- **生成予算:** LLM API は総額 **10,000円以内**。6,000円を自動停止上限、
  4,000円を再実行・翌年の更新用に留保する。
- **公開意思:** LLM 生成作文、プロンプト、生成メタデータ、分析コードを、権利と
  利用規約の許す範囲で再利用可能な研究データとして公開する。
- **解釈契約:** 出力はテキストの記述的特徴であり、評点、CEFR、語彙知識、執筆者
  熟達度、作文品質、AI 生成確率ではない。外的評定との関連を研究しても、この契約を
  変更せず、予測モデルを製品へ組み込まない。

24か月の成功は、論文数ではなく次の順で判定する。

1. v1.0 と後方互換な v1.x が、CI・資源ハッシュ・方法カードとともに公開される。
2. 第三者が公開例文と合成ベンチマークから全指標を再計算できる。
3. ELLIPSE を公式配布元から取得すれば、公開コードで外的関連の分析を再実行できる。
4. 日本語を含む利用文書、CITATION.cff、Zenodo DOI、保守方針がそろう。
5. 査読論文は上記資産を説明・検証する範囲で1本を主目標、追加論文は条件付きとする。

2026-07-24の履歴監査で、旧GitHub originの過去commitから除外資源へ到達できると判明した。
旧repoをprivateで保持し、同じcanonical URLへclean-history public repoを新設した。
root treeと全blob identity、hosted CI、main保護の証拠は
[`public-history-migration.md`](public-history-migration.md)に固定した。

**24か月の非目標:** 指標数の最大化、TAALES 全機能の複製、CEFR/評点の自動推定、
AI作文検出、匿名の実作文 SaaS、新規学習者コーパス収集、4本の論文を前提とした
自己引用チェーン。

---

## 1. 三層の検証アーキテクチャ

単一データセットにすべての妥当性を負わせず、目的の異なる三層を分離する。

| 層 | データ | 答える問い | 再現性 | 製品への影響 |
|---|---|---|---|---|
| **L1 計算検証** | 手作業・決定論的変形・golden fixtures | 式、境界条件、長さ補正、資源版固定が正しいか | byte-for-byte を要求 | CI のリリースゲート |
| **L2 測定特性** | 公開 LLM 生成作文 | 指標がレジスター、反復、長さ、話題、ジャンル、モデルにどう反応するか | 公開原文からの分析再現を保証 | 解釈カードと警告を改善 |
| **L3 外的関連** | 公式 ELLIPSE 人間作文 | 指標が人手の語彙評定とどの程度関連し、既存指標を超える情報を持つか | fetch adapter、版・hash・解析コードを公開 | 得点予測機能は追加しない |

この構造により、LLM作文を人間熟達度の代用品にせず、同時に人間データへの外的な
アンカーを失わない。L1 はソフトウェア検証、L2 は測定器のストレステスト、L3 は
構成概念との関連証拠であり、三者を「妥当性」という一語で混同しない。

---

## 2. 人間作文コーパスの採否

無料取得と再配布可能性は別に判定する。人間作文は公開アプリのランタイム資源ではなく、
ローカル研究用 benchmark adapter として扱う。

| コーパス | 内容と評定 | アクセス・条件 | 24か月での決定 |
|---|---|---|---|
| **ELLIPSE** | ELL 6,482作文、公開CSVで44 prompts、総合評定と Vocabulary/Cohesion/Syntax/Phraseology/Grammar/Conventions | [公式GitHub](https://github.com/scrosseye/ELLIPSE-Corpus)、CC BY-NC-SA 4.0、[無料プレプリント](https://zenodo.org/records/11217937) | **L3 の主ベンチマーク。ローカル実データ監査済み** |
| **PELIC v1.1** | 46,230テキスト、L1・course level・placement test・縦断情報。作文単位の独立人手評定ではない | [Zenodo](https://zenodo.org/records/4577423)、CC BY-NC-ND 4.0。現在のローカルZIPはGit LFS pointerのみ | v1.1以降の既知群・縦断複製候補 |
| **PERSUADE 2.0** | 25,996作文、総合作文品質、談話要素、ELL status。語彙下位評定はない | [公式GitHub](https://github.com/scrosseye/persuade_corpus_2.0)、CC BY-NC-SA 4.0。現在のローカルZIPはREADMEとrubricのみ | 規模・公平性の任意頑健性アーム |
| **ICNALE / GRA** | アジア学習者、統制 prompt、CEFR帯・分析評定 | 無料登録制、原文再配布禁止 | 日本・アジア複製は v1.1 後 |
| **EFCAMDAT / W&I** | 大規模 CEFR 対応または CEFR 評定 | 申請・非移転・派生物公開制限 | 公開再現性が弱いためクリティカルパス外 |

ELLIPSE はリポジトリへ vendor しない。schema v1.1 の `fetched`・
`evaluation-benchmark` として、取得元、取得日、上流 commit、ファイルサイズ、
SHA-256、ライセンススナップショットを manifest に記録する。元作文、個人属性、
学習済み予測器は Git、パッケージ、コンテナ、アプリ、CI artifact に含めない。
公開するのは fetch/validation/analysis コード、除外規則、集計結果、再現手順である。

この境界は2026-07-23に実装済みである。データ契約は
[`benchmarks/ellipse/manifest.json`](../benchmarks/ellipse/manifest.json)、取得・検証は
[`scripts/fetch_ellipse.py`](../scripts/fetch_ellipse.py)、機械可読な事前分析仕様は
[`benchmarks/ellipse/analysis-plan.json`](../benchmarks/ellipse/analysis-plan.json)、
判断理由は
[`ellipse-confirmatory-analysis-spec.md`](ellipse-confirmatory-analysis-spec.md)を正本とする。
`--download`はnetwork有効時にopaqueな外側ZIPのsize/hashだけを検証して無視対象領域へ
置き、中身を開かない。networkを無効化した後、`--source`で内容を検証する。この検証
だけなら出力を作らず、明示的な`--provision`時だけ`.research/`へ最終train/test CSVと
個票を含まない検証記録を置く。指標と評定の関連値はまだ計算しない。

2026-07-23 のローカル監査では、ELLIPSE の最終 train 3,911本と test 2,571本、
全評定、rubric、raw rater file の実在とhashを確認した。一方、PELICのCSVはすべて
Git LFS pointer、PERSUADEは外部データへのリンクとrubricだけであった。詳細と固定値は
[`benchmark-resource-audit-2026-07-23.md`](benchmark-resource-audit-2026-07-23.md)
を正本とする。したがって後二者を取得する作業はv1.0を待たせない。

ELLIPSE の44 promptsはすべて提供済み train/test の両方に現れるため、未知 prompt
一般化は prompt 単位の grouped split で評価する。論文の要旨には29 promptsとの記載も
あるが、公開CSV、本文の平均値、Figure 3は44で一致するため、運用上は44を採用して
不整合を注記する。主要目的変数は final CSV の Vocabulary 平均評定、総合評定は副次、
PERSUADE の総合品質は代替語彙評定として扱わない。

Vocabulary の裁定前 Cohen's kappa は `.518` であり、最終MFRMの作文能力信頼性 `.94`
とは別の統計である。人手評定を誤差のないgold standardと呼ばず、prompt-clusteredな
不確実性と感度分析を報告する。raw評定からfinal得点を再構成せず、ID不一致14件と
0の符号化を解決するまでraw fileは副次的な信頼性分析だけに使う。

---

## 3. 公開 LLM 作文ベンチマーク

2026-07-24時点では、
[`pilot-protocol.json`](../benchmarks/synthetic/pilot-protocol.json) と
[`synthetic-pilot-protocol.md`](synthetic-pilot-protocol.md) にpilotの事前仕様だけを
固定した。状態は `specified_not_executed` であり、外部API呼出し0回、費用0円、生成作文
0本である。現段階のGit公開allow-listもprotocol JSON 1ファイルだけで、作文、attempt、
request/response、backupは、権利・秘密情報・QCの別審査を通すまで追跡を拒否する。

### 段階設計

1. **pilot（48作文）:** 12 topics × 2 genres（argumentative/expository）×
   2 registers（plain/formal-academic）× 1 model snapshot × 1 replicate。API、長さ遵守、
   拒否、manifest、費用停止を検証する。上限と同額になる次のrequestも送らない。
2. **core v1（432作文、別承認）:** 12 topics × 2 genres ×
   3 registers（plain/neutral/formal-academic）× 2 model snapshots × 3 replicates。
   pilot結果を見た後に、別versionのprotocol、権利審査、費用承認を先に固定する。
3. **拡張（最大864作文）:** 追加2モデルは、自動生成・QC・公開処理が完成し、
   v1.0公開を遅らせず、別protocolと残予算の事前gateを満たす場合だけ起動する。

pilotの自動停止は予約済みまたは確定済み累積額が6,000円以上、プロジェクトhard capは
10,000円である。4,000円の留保はpilotの自動再開やcoreへの自動移行に使わない。

各作文は約250語に固定する。100/150/200 token の接頭部分を決定論的に派生させ、
追加API費用なしで長さ感度を測る。plain/formal 指示は指標応答性の直接操作であり、
語彙洗練度の独立した正解ラベルとは呼ばない。CEFR水準や「L2学習者らしく」という
人間能力の擬態指示は使わない。

モデルは生成開始14日前に、公式の価格・利用規約・固定 snapshot の提供状況を再監査
して凍結する。最新版競争はしない。最低1つの日付固定 API snapshot を歴史的アンカー
とし、追加モデルはモデル間頑健性のためだけに使う。pilot 後に指標値を見て prompt を
調整してはならない。本番の拒否、失敗、語数逸脱も削除せず、QC flag として保存する。

### 再現性の三段階

- **分析再現性（保証）:** 公開した原文、固定ツール commit、lockfile、資源 hash から
  同じ指標を再計算できる。
- **生成追試可能性（best effort）:** prompt、model ID、設定、seed対応状況から再要求
  できる。
- **文字列一致（非保証）:** 閉鎖 API の配信基盤更新があるため保証しない。

各行に document ID、完全な prompts と hash、provider、要求/応答 model ID、UTC時刻、
request ID、SDK版、全生成設定、retry、finish reason、token使用量、費用、生の
request/response、原文と正規化文の hash、QC、ツール commit、資源 hash を JSONL で
記録する。秘密鍵は保存しない。データカードには利用規約の取得日、AI出力の権利に
関する地域差、第三者著作物との非類似を保証しないことを明記する。

---

## 4. 成果物ポートフォリオ

| 優先 | 成果物 | 完了条件 |
|---|---|---|
| **1** | **公開ツール v1.0** | CI、golden fixtures、release gate、方法/解釈カード、lockfile、日英の最小文書、CITATION.cff、Zenodo DOI |
| **2** | **Synthetic Benchmark v1** | prompts、生成文、JSONL provenance、データカード、生成・分析コード、immutable release DOI |
| **3** | **Validation Report** | L1–L3を一つの再実行可能な report に統合。ELLIPSE raw data は fetch-only |
| **4** | **ツール論文1本** | v1.0 と report を説明し、再現性・測定特性・外的関連を過大主張せず報告 |
| 条件付き | Dispersion 方法論文 | v1.0保守を遅らせず、tuple が独立貢献としてまとまる場合のみ |
| 条件付き | 外的妥当性の独立論文 | ELLIPSE結果が事前基準を満たし、ツール論文と重複しない場合のみ |

別論文を作るために指標や分析を増やさない。Validation Report は査読の有無にかかわらず
公開し、負の結果もツールの解釈境界として価値のある成果にする。

---

## 5. 統合クリティカルパス（M0 = 2026-08）

| 時期 | マイルストーン |
|---|---|
| **M0–1** | clean-history public root、hosted CI、main保護、CPython 3.12.13、Alpine 3.23 Linux x86_64 wheel hash、Python base-image manifest、runtime identity check、独立したapplication/output-schema版、tag release gate、北極星・非目標・not-a-detector、実資源を使うgolden fixtures、決定論的JSON/XLSX serialization、clean application imageのdigest・provenance・Critical例外ゼロscanまで完了 |
| **M1–2** | v1.0 指標・出力・非目標の機械可読scope、registry schema v1.1、監査済みELLIPSE hash・commit・license evidence、fetch/verify manifest、事前分析仕様を固定済み。新しい分散指標のproduction移植はv1.x以降の条件付き課題へ移す |
| **M2–3** | v0.9 release candidate。clean buildから得たapplication-image digest・provenance・scan、全公開指標の方法・解釈カード、CLI/ローカル起動、公開例文を固定。v1.0を遅らせず全事前gateが通る場合だけSynthetic pilot 48本を別runで実行 |
| **M3–4** | **v1.0 + CITATION.cff + Zenodo DOI 公開**。完全な公開インベントリ監査、再現手順をクリーン環境で検証 |
| **M4–6** | Synthetic core 432本を immutable release。事前固定した ELLIPSE 分析をローカル実行。L1–L3 Validation Report を公開 |
| **M6–8** | v1.0利用経験と report を基にツール論文を投稿。README-ja、バイリンガル Help、日本語プライバシー通知を完成 |
| **M8–12** | 保守・issue対応を優先しながら、軽量な client-side Panel A を試作。通常版との同値性が保てる範囲だけ公開 |
| **M12** | 公開Web版の形態を go/no-go 判断。既定はローカル版＋合成/公開例だけのデモ。実作文 server pilot は機関需要と承認がある場合のみ |
| **M12–24** | v1.x保守、年1回の小さな sentinel 生成、依存/資源更新、利用文書改善。余力時のみ PELIC/ICNALE 複製または別論文 |

MASC/OANC/ANC、AntBNC許諾、完全な TAALES-open、新しい汎用dispersion tuple、Cloud Run、
全面UI i18n は v1.0 のゲートではない。安全で再現可能な現行 green 資源だけで公開価値が
成立する。

---

## 6. 工学・統治上の停止規則

- 新指標は、既存指標と異なる構成概念、式、必要資源、長さ感度、解釈上の追加価値を
  1枚のカードで示せない限り追加しない。
- 新規参照コーパスは、現行 green 資源で答えられない問いを明示できない限り、
  クリティカルパスへ入れない。
- benchmark data と runtime data を同じ registry flag、ディレクトリ、build context、
  配布判断で扱わない。
- ELLIPSE や他の人間作文を外部 LLM API に送信しない。分析はローカルで行う。
- 評点予測器、CEFR分類器、AI検出器を学習・配布・UI表示しない。
- 2週間で権利・取得問題が解けない資源は defer し、v1.0を待たせない。
- 追加論文が保守、文書、releaseを4週間以上遅らせる場合、その論文を延期する。
- API本番生成は、見積上限・課金アラート・停止スイッチ・pilot完了の4条件がそろうまで
  開始しない。

---

## 7. 主要リスクと長期対策

| リスク | 対策 |
|---|---|
| 目的が熟達度評価へずれる | READMEの解釈契約、not-a-detector、研究コードと製品コードの分離をrelease gate化 |
| 指標・コーパスの追加が公開を遅らせる | v1.0 scope freeze、2週間timebox、停止規則、optional trackの明示 |
| 閉鎖LLMの完全再生成不能 | 原文・request/response・hashを公開し、分析再現と生成再現を区別。sentinelだけ定期再生成 |
| 合成条件が循環的 | 操作への応答性だけを主張し、ELLIPSE人手評定を独立アンカーにする |
| NC/SA/NDデータがMIT製品へ混入 | fetch-only research tier、raw payload非同梱、NOTICEとmanifest、公開前inventory監査 |
| バス係数=1 | 自動CI、1コマンド再構築、継承メモ、Zenodo release、issue/保守方針 |
| DropboxとGitの競合・権利ファイル復活 | legacy Dropbox checkoutとclean public repoを分離済み。公開変更はclean cloneのPRだけに限定し、public-history gateとbranch protectionで復活を阻止 |
| API・依存・資源の価格/版ドリフト | 取得日・snapshot・lockfile・hashを固定し、更新は新versionとして追加。旧releaseを上書きしない |

---

## 8. 四半期メタ認知レビュー

3か月ごとに、新規作業へ入る前に次を1ページの decision log に答える。

1. この作業は90日以内に公開ツール、再現性、利用者理解のどれを改善するか。
2. 第三者が、著者への連絡なしに同じ入力から結果を再計算できるか。
3. 追加される保守面（依存、資源、API、UI、文書、権利）は何個か。
4. 新しい指標・コーパス・論文は、既存資産では答えられない問いを本当に持つか。
5. 結果が負でも公開価値が残る設計か。
6. 今期に**やめること**は何か。

意思決定は、可逆なもの（追加分析、optional adapter）と不可逆または高コストなもの
（公開API契約、データ配布、永続スキーマ、個人データ処理）を分ける。不可逆判断は
証拠と停止条件を先に書き、可逆判断は小さな pilot で学習してから拡張する。

---

## 9. 直近30日の実行順

2026-07-23時点で、旧項目3のregistry schema v1.1とresearch-only fetched境界、旧項目4の
ELLIPSE manifest・安全なfetch/verify adapter・分析仕様固定は前倒しで完了した。ここでは
「関連分析を実行した」ことと「実行条件を固定した」ことを区別し、後者だけを完了とする。

1. **完了:** clean-history rootを公開し、当初のCPython 3.12.10とLinux x86_64 wheel hashの固定依存CIでpytest、release gate、benchmark
   混入検査、checkout不変検査を実走した。CPython 3.12.13、Alpine 3.23、musllinux lock、Critical例外ゼロへ更新後もmainのCIとCandidate gateを再通過した。
2. **完了:** v1.0の指標・出力schema・解釈境界・非目標を機械可読に凍結する。
3. **仕様完了・生成未実行:** Synthetic pilot のprotocol、全prompts、provenance schema、
   QC、retry、秘密情報境界、費用停止を固定する。
4. **candidate image gate完了:** v1 scopeからcanonical golden fixturesと
   serialization regressionを作成し、package wheel hashとPython base-imageの
   linux/amd64 manifest digestを固定した。clean checkoutのapplication imageで
   digest、provenance、SBOM、Critical例外ゼロscan、golden再現を記録した。次はこの証拠を使ってv0.9 candidate packageを構築する。新しい分散指標はここへ混ぜない。
5. v1.0候補のclean-environment再構築を通した後に限り、ELLIPSEをローカルへ明示取得し、
   outcome-blind feature QCまで進める。評定との関連計算は事前ゲート通過後の別実行とする。

最初の公開判定点は「論文を書けるか」ではなく、**新しい環境で v1.0候補を取得し、
権利確認済み資源だけから同じ出力を再生成できるか**である。
