# ELLIPSE確認的分析 事前仕様

版: 1.0.0
固定日: 2026-07-23
状態: **確認的アウトカム分析前に固定**

本書は、ELLIPSEを用いる外的関連分析について、結果を見てから指標、除外、モデル、
検定を選ぶ余地を制限する事前仕様である。機械可読な正本は
[`benchmarks/ellipse/analysis-plan.json`](../benchmarks/ellipse/analysis-plan.json)
であり、本書はその判断理由と解釈境界を日本語で説明する。

この分析の目的は公開ツールの記述的指標を監査することであり、作文評点、CEFR、
執筆者熟達度を推定する製品を作ることではない。分析結果は、良好でも不良でも
公開ツールv1.0のリリースゲートにしない。

## 1. 研究質問

### 主要研究質問

長さ、MTLD、NGSL Beyond-K2を含む固定baselineに、TUBELEXのtype-weighted
frequencyとchannel prevalenceを加えたとき、未知promptにおけるVocabulary評定の
macro-MAEが改善するか。

### 確認的副次研究質問

- promptを調整した関連モデルで、低いTUBELEX type-weighted Zipf frequencyは
  高いVocabulary評定と関連するか。
- 同じモデルで、低いTUBELEX type-weighted channel prevalenceは高いVocabulary評定と
  関連するか。

### 副次・探索的研究質問

- `Overall`評定に対する関連を副次的に記述する。
- `Cohesion`、`Syntax`、`Phraseology`、`Grammar`、`Conventions`に対する関連を
  探索的に比較する。

## 2. データと分析対象

公式final train 3,911本とfinal test 2,571本を結合した6,482作文をprimary datasetと
する。6,482の作文IDは一意であり、trainとtestのIDおよび本文の完全一致重複はない。
分析単位は作文、未知条件の一般化単位はpromptである。

| 項目 | 固定値 |
|---|---:|
| 作文数 | 6,482 |
| prompt数 | 44 |
| task | Independent |
| primary text field | `full_text` |
| primary outcome | `Vocabulary` |
| Vocabulary尺度 | 1.0–5.0、0.5刻み |
| Vocabulary欠損 | 0 |
| prompt欠損 | 0 |
| 本文欠損 | 0 |
| 既知のその他欠損 | testの`SES` 1件 |

44 promptは提供済みtrainとtestの両方に現れる。このため、提供済み分割は
「未知prompt」の証拠には使わず、44-fold leave-one-prompt-out（LOPO）を使う。
プレプリント要旨の29 promptという記述は、公開CSV、方法節、本文のprompt平均と
一致しないため採用しない。

### データ同一性

confirmatory runの前に、少なくとも次のhashを検証する。

| ファイル | records | SHA-256 |
|---|---:|---|
| `ELLIPSE_Final_github_train.csv` | 3,911 | `782344e99668a3ff508d7410c0eb6e36da70f3b28f81c96e367f1ca04924b06c` |
| `ELLIPSE_Final_github_test.zip` | — | `9ecddfcda83f6a99c8a24dda47fb1c30673fedeaf578a0fadac6645fd151a2f8` |
| `ELLIPSE_Final_github_test.csv` | 2,571 | `7e990c6392a9df9554d15bdd22f0b568d19095cd6676ad39cb1eaa69c977ed7a` |

ファイル、件数、schema、尺度のいずれかが一致しない場合、結果を推定する前に停止する。

### 取得と内容検証の分離

取得時のnetwork接続と、人間作文を開く処理を同じ段階にしない。まずnetwork有効環境で
外側ZIPをopaqueなファイルとして取得し、sizeとSHA-256だけを検証する。この段階では
ZIP memberを開かない。

```bash
python3 scripts/fetch_ellipse.py --download
```

次にnetworkとtelemetryを無効化し、取得済みZIPを`--source`へ渡す。test ZIPのpasswordは
upstream READMEを参照し、manifest、shell history、分析logへ値を記録せず、安全なローカル
fileまたは対話入力から渡す。`--provision`を付けない検証では出力directoryを作らない。

```bash
python3 scripts/fetch_ellipse.py \
  --source .research/benchmarks/ellipse/sources/ELLIPSE-Corpus-dc3b8f0b.zip \
  --test-password-file /secure/local/path/ellipse-test-password.txt \
  --provision
```

内容検証はexact ZIP inventory、unsafe path、member size/hash、暗号化test CSV、26列、
6,482行、44 prompt、ID重複、本文完全一致重複、score尺度、既知missingnessをすべて確認する。
raw rater ZIPはopaque memberとしてhashだけを検証し、復号・展開・保存しない。

### 使用しないデータ

raw rater fileはprimaryにも感度分析にも用いない。理由は、評定0の符号化が未解決、
finalとrawに対応しないIDが双方14件ずつ存在し、2評定者の単純平均がfinal評定を完全には
再現しないためである。

## 3. アウトカム

### 主要アウトカム

final CSVの`Vocabulary`を1.0–5.0の連続変数として扱う。これは2名の訓練済み評定者に
基づく分析評定だが、誤差のないgold standardとはみなさない。裁定前Vocabularyの
Cohen's kappaは`.518`であり、最終コーパス全体に対するMFRM信頼性`.94`とは異なる。

`.518`は連続得点の信頼性係数ではないので、平方根で割るなどの機械的な希薄化補正は
行わない。9段階の順序カテゴリとして扱うモデルは感度分析に限定する。

### 副次・探索的アウトカム

- `Overall`: 事前指定した副次アウトカム。
- `Cohesion`、`Syntax`、`Phraseology`、`Grammar`、`Conventions`: 探索的アウトカム。

副次・探索的アウトカムの結果は、Vocabularyの主要結果を置き換えない。

## 4. 指標と設定の固定

分析開始前に、ツールcommit、依存lockfile、資源manifest、資源hashを記録する。
指標は次の5つに固定する。

| 役割 | 出力path | 構成概念・処理 | Vocabularyとの期待方向 |
|---|---|---|---:|
| length control | `n_tokens` | `log`後、自然三次spline df=3 | 調整のみ |
| diversity control | `panel_a.mtld` | threshold=.72、z標準化 | ＋ |
| frequency-list control | `panel_b.pct_beyond_k` | NGSL 1.2、open_flemma、K=2、z標準化 | ＋ |
| focal 1 | `tubelex.frequency_zipf_type_mean` | type-weighted add-one Zipf、z標準化 | **−** |
| focal 2 | `tubelex.channel_log10_prevalence_type_mean` | type-weighted Beta(1,1) log prevalence、z標準化 | **−** |

ツール設定は`unit=token`、`frequency_list_id=ngsl`、
`lemmatizer_name=open_flemma`、`advanced_cutoff=2`、`mtld_threshold=.72`とする。
TUBELEXはcommit `7cb5fb36add76b83a266d1967536e1a1d3faa513`のTreebank variantと、
現在のadd-one/Beta(1,1)方式を用いる。

type weightingを主要とするのは、Vocabulary評定に対して、同一語の反復量よりも
語彙レパートリーの構成を優先するためである。channel prevalenceをvideo prevalenceより
優先するのは、同一channel内の大量投稿の影響を抑えるためである。

### 指標選択規則

- outcomeやoutcomeとの相関を見て指標を追加、削除、置換しない。
- focal指標は上記2つだけとする。
- TTR、MATTR、HD-D、vocd-Dは、MTLDとの重複と多重性を抑えるため確認的集合に
  入れない。
- TUBELEXのtoken-weighted値とvideo prevalenceは感度分析専用とする。
- OEWN polysemy/depth、P_Lex、S、Advanced Guiraud、off-list率は探索的にのみ扱う。
- focal 2指標のVIFが10を超えても、結果に基づく片方の削除はしない。B0対B1の
  2指標block比較は維持し、個別の独立寄与を主張しない。単独focalモデルは
  感度分析として両方を対称に示す。

## 5. モデル

### B0: baseline

```text
Vocabulary ~ natural_cubic_spline(log(n_tokens), df=3)
           + z(MTLD)
           + z(NGSL Beyond-K2)
```

### B1: augmented

```text
Vocabulary ~ B0
           + z(TUBELEX type-weighted Zipf)
           + z(TUBELEX type-weighted channel prevalence)
```

B0とB1はordinary least squaresで推定し、同一のcomplete-case集合で比較する。
変数選択、結果に基づく変換、hyperparameter tuning、予測値の1–5へのclipは行わない。

### prompt調整関連モデル

個別係数の関連を推定するときは、B1に43個のprompt fixed effectsを加える。連続予測子は
分析対象全体でz標準化し、係数を「指標1 SD増加あたりのVocabulary評定点差」として示す。

標準誤差はpromptをclusterとするCR2、自由度はSatterthwaite近似、区間は95%とする。

## 6. 未知prompt検証

44 promptを1つずつheld outし、43 promptでB0とB1を学習する。各foldにおいて、
z標準化の平均・SDとlength splineのknotsはtrain側だけから求める。held-out側の
Vocabulary、prompt固有切片、prompt dummyはモデルへ入力しない。各作文は1回だけ
held-out予測を受ける。

### 主要performance endpoint

各promptでMAEを計算し、44 promptを等重みで平均したmacro-MAEを用いる。

```text
delta_macro_MAE = macro_MAE(B0) - macro_MAE(B1)
```

正値がTUBELEX blockによる改善を表す。作文数の多いpromptに結論を支配させないため、
essay-weighted MAEは副次指標とする。

95% CIは44個のprompt別paired差を単位とするnonparametric bootstrap 10,000回で求め、
seedを`20260723`に固定する。essay-level bootstrapは行わない。

- 95% CI下限が0より大きい: 増分的情報の証拠。
- 点推定が`.05`以上: 最小観測刻み0.5の10分の1を超える改善。
- CI下限は0を超えるが点推定が`.05`未満: 検出可能だが小さい改善。
- CIが0を含む: baselineを超える増分的証拠なし。

macro-RMSE、essay-weighted MAE/RMSE、out-of-sample R-squaredは副次的に報告する。

## 7. 推定と多重性

### 主要family

Vocabularyに対するB0対B1の`delta_macro_MAE`のみを主要検定とし、alpha=.05とする。
単一検定なので補正しない。

### focal係数family

Vocabularyに対する2つのTUBELEX係数は、両側検定2件にHolm補正を適用し、
family-wise alpha=.05とする。期待方向と逆の有意係数を、仮説を支持するものとは扱わない。

### 副次アウトカムfamily

Overallと残る5分析評定に対する2 focal係数、計12件にはBenjamini-Hochberg FDR
`q=.05`を適用する。感度分析から確認的p値を追加しない。

## 8. 欠損、除外、計算不能

primary populationは監査済みfinal 6,482作文全体であり、短文も含める。50 tokens・
20 typesというTUBELEX警告は検証済み除外基準ではないので、primaryから機械的に
除外しない。

- outcomeとpredictorの補完は行わない。
- confirmatory指標の計算不能が0より多く1%未満なら、B0とB1共通のcomplete-caseで
  分析し、件数とprompt分布を報告する。
- 計算不能が1%以上、1つのpromptが完全に失われる、または0-token作文があれば停止する。
- primaryではwinsorize、外れ値除外、追加の綴り訂正、結果を見てからの本文正規化を
  行わない。
- demographic感度分析では、SES欠損1件を含むケースをcomplete-case除外する。

## 9. 必須感度分析

1. `Vocabulary`を9順序カテゴリとするcumulative-logitモデル。
2. 50 TUBELEX lookup tokensかつ20 lookup types以上に限定。
3. 各promptの総weightを等しくする関連モデル。
4. focal 2指標をtoken-weighted値へ置換。
5. channel prevalenceをtype-weighted video prevalenceへ置換。
6. length splineを線形`log(n_tokens)`へ置換。
7. ツール`n_tokens`をELLIPSEの`num_words`へ置換。
8. grade、gender、race/ethnicity、SESを追加調整。ただし人口属性係数を因果解釈しない。
9. `n_tokens`の上下1%を除外。
10. 提供済みtrain/testで評価。ただし既知prompt感度分析と明記する。
11. promptを1つずつ除外し、係数とdelta-MAEの範囲を示す。
12. VIFが10を超えた場合、結果で選択せず、2つのsingle-focalモデルを対称に示す。

感度分析は方向、大きさ、CIの安定性を見るためのものであり、主要結果を都合よく
置き換えない。

## 10. 停止規則

### 分析開始前の停止

次のいずれかが生じた場合、confirmatory estimationへ進まない。

- CSV/ZIPのSHA-256、件数、44 prompt、required fields、Vocabulary尺度が固定値と異なる。
- ツールcommit、lockfile、資源hash、パラメータが記録されていない。
- frozen tool commitの計算テストまたはresource verificationが失敗する。
- outcomeを除いたfeature QCが完了していない。
- 人間作文処理時のnetwork、telemetry、外部logging、外部APIを無効にできない。

### 分析中の停止

- 本文、作文ID、個票指標、個別予測が外部API、telemetry、外部log、公開artifactへ
  到達した可能性があれば、直ちに停止しprivacy incidentとして扱う。
- focal指標の実装修正が必要、計算不能が1%以上、promptが失われる、固定design matrixが
  特異になる場合、別指標へ置換せず停止する。
- 修正後に再開する場合は、日付・理由を記録した新しいplan versionにする。

### 結果に基づいて停止・拡張しない

null、弱い、期待と逆という理由で、指標追加、除外変更、モデル変更、確認的再実行を
行わない。結果はそのまま解釈境界として公開する。

L3作業が10稼働日を超える、またはv1.0公開を遅らせる場合は、ELLIPSE分析を延期して
公開ツールを優先する。

## 11. privacyと公開物

ELLIPSE本文はnetworkとtelemetryを無効化したローカル環境でのみ処理する。本文、
作文ID、個人属性、作文単位指標、個別予測、fitted modelをGit、CI artifact、package、
container、Zenodo、公開アプリへ含めない。

公開できるのは次に限る。

- 公式ファイルを利用者自身が取得するfetch/verification code。
- 分析コードと本仕様。
- 資源・ソフトウェアhash。
- 集計記述統計、集計係数、CI。
- prompt単位に集約したvalidation performance。

分析用モデルは個別採点機能として配布・保存・UI表示しない。ELLIPSEデータ自体も
公開ツールへ同梱しない。

## 12. 主張境界

### 許容する主張

結果が支持する場合でも、主張は次の範囲に限定する。

> 事前指定した記述的語彙指標が、監査済みELLIPSEの米国中高生ELLによる独立作文に
> おいて、誤差を含む人手Vocabulary評定と関連し、44 prompt間で一定の増分情報を
> 示した、または示さなかった。

すべての効果は、固定したtokenizer、normalizer、参照資源、パラメータ、prompt、
対象集団に条件づけられる。

### 禁止する主張

- ツールが語彙知識、CEFR、執筆者熟達度、作文品質を直接測る、または推定できる。
- 観測関連が因果的であり、指標値を変えれば評定が向上する。
- TUBELEX普及度が親密度、教育上の望ましさ、語の良し悪しを直接表す。
- ELLIPSE内LOPOが成人、L1話者、発話、学術ジャンル、別コーパスへの一般化を保証する。
- Vocabulary評定が誤差のないgold standardである。
- 評価用fitted modelを公開採点機能として配布・展開すべきである。

nullまたは弱い結果も公開し、ツールの解釈契約を狭める有用な証拠として扱う。

## 13. 修正履歴と確認的状態

結果へアクセスする前の実装上の明確化は、日付、変更理由、影響箇所を記録して
plan versionを上げる。結果へアクセスした後の指標、除外、モデル、検定の変更は
確認的分析とは呼ばず、探索的分析として明示する。
