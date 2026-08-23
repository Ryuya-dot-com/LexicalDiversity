# 公開 v1.0 指標・出力スコープ凍結

凍結日: 2026-07-24
対象: `1.x` release line
機械可読契約: [`v1-metric-scope.json`](v1-metric-scope.json)
トークナイザ契約: [`tokenizer-contract.json`](tokenizer-contract.json)

注: 指標集合は引き続き application v1.0 候補の scope だが、短文時の silent parameter
reduction と出力既定値を是正したため、公開出力契約は SemVer MAJOR の `2.0.0` である。
既存リンクを壊さないため `v1-metric-scope.json` という歴史的ファイル名は今回維持し、
ファイル名の整理は別課題とする。

## 1. この文書の役割

公開 v1.0 は、指標数を増やすことではなく、現在すでに動作し、テストでき、必要な
ランタイム資源の権利と版を確認できる記述的プロファイラを再現可能な形で公開する。
本書は、そのための指標、資源、JSON/Excel 出力、解釈可能な主張、非目標を固定する。
ロードマップにある将来候補を、実装済み機能として先取りしてはならない。

契約の不一致が生じた場合は、まず
[`test_v1_metric_scope.py`](../tests/test_v1_metric_scope.py) が実コードから検証する事実を
確認し、次に機械可読 JSON、本書の順で整合させる。これは v1.0 候補の契約であり、
現在の開発中パッケージ番号そのものを「すでに v1.0 公開済み」とみなす宣言ではない。

## 2. v1.0 に含める指標

### Panel A: 語彙多様性・反復

Panel A は参照語彙表を使わず、既定の `english_unicode_v1` で得た小文字化
surface token 列を対象にする。この方針は NFC、Unicode letter と後続 mark、一般的な
typographic apostrophe の ASCII `'` への正規化を固定し、hyphen/dash と数字は token を
分割し、数字だけの列は除外する。旧 ASCII 正規表現は `ascii_legacy_v1` を明示した場合
だけ使える。12個の公開 key と順序を次に固定する。

| key | 表示名 | 多様性との表示上の向き |
|---|---|---|
| `ttr` | TTR | 高いほど多様 |
| `rttr` | RTTR / Guiraud | 高いほど多様 |
| `cttr` | CTTR | 高いほど多様 |
| `herdan` | Herdan C | 高いほど多様 |
| `maas` | Maas | **低いほど多様** |
| `msttr` | MSTTR | 高いほど多様 |
| `mattr` | MATTR | 高いほど多様 |
| `mtld` | MTLD | 高いほど多様 |
| `hdd` | HD-D | 高いほど多様 |
| `vocd` | vocd-D | 高いほど多様 |
| `yule_k` | Yule's K | **低いほど多様** |
| `yule_i` | Yule's I | 高いほど多様 |

各 metric は従来の scalar 値に加え、`panel_a_records` で `value`, `status`,
`missing_reason`, `method_id`, `requested_parameters`, `effective_parameters`,
`advisory_quality_floor_tokens`, `advisory_quality_status` を返す。標準 method ID の
MSTTR/MATTR/HD-D/vocd-D は、短文に合わせて segment/window/sample/range を縮小しない。
指定した計算 domain を満たさなければ標準値は `null` となり、必要なら明示的な
`*_adaptive` API variant を別 key・別 method ID として利用する。missing record の
`effective_parameters` は空 map とし、実値を適用したと誤認させない。available record
だけが実際に適用した parameter を記録する。

公開 metric API の token 入力は、空でないUTF-8文字列だけを要素に持つ pre-tokenized
materialized sequence に限定する。要素内の空白を再tokenizeはしない。bare string/bytes、
generator、非文字列要素、空文字、UTF-8へencodeできない文字列は計算前に拒否し、boolean option は exact `bool`、
整数 parameter は `bool` や float から暗黙変換しない。固定 method ID を持つ record API の
MTLD minimum factor length は10であり、別値は method identity と矛盾するため拒否する。

project minimum token 数は値の有効性を保証する cut score ではなく、計算 domain から
独立した表示上の注意喚起である。したがって Yule 指標などは advisory floor 未満でも
式が定義されれば計算し、警告なしを信頼性・妥当性の証明としては扱わない。Python MTLD
は factor 長10以上で TTR が threshold **以下**（`<=`）になった時に factor を閉じる
双方向平均 variant として固有 method ID を持ち、strict `<` の R variant との数値同等性を
主張しない。10 token 未満は計算 domain 外として `null` にする。

### Panel B: 選択語彙表との一致

Panel B は選択した参照語彙表と fallback normalizer に依存する。現行の
`surface_first_rank_lookup_normalized_fallback_v1` は、小文字化した surface をまず
語彙表 key に照合し、不一致の場合だけ normalizer 出力を再照合する hybrid
方式である。direct hit は再 normalization しないため、pure flemma/lemma pipeline ではなく、
LexTutor との数値的同等性も主張しない。公開 top-level key は次の9個である。

| key | 内容 |
|---|---|
| `mapping_diagnostics` | hybrid mapping path の token 数/率と surface/mapped unit type の集計 |
| `lfp` | band 別 token/type 数、coverage、累積 coverage |
| `coverage_threshold` | 90/95/98% 等へ最初に到達する選択語彙表 band |
| `advanced_guiraud` | advanced type 数を `sqrt(N)` で割った値 |
| `pct_beyond_k` | cutoff を超える distinct family/head の割合 |
| `mean_rank` | in-list の mean rank、mean log-rank、off-list token 率 |
| `p_lex` | 完全な10語 segment に対する Poisson fit |
| `s_index` | 選択語彙表 rank に対する coverage curve fit |
| `band_wise` | band ごとの token/type 数と MTLD/MATTR/HD-D |

`mapping_diagnostics` は直接 hit、normalized fallback hit、normalized off-list、identity
fallback の相互排他な集計と type 圧縮数だけを含む。入力語、normalizer 出力語、
head/rank 列は含めない。`_mapped` のような一時的な token-to-head 対応も公開 payload に
含めない。P_Lex は
完全な10語 segment がない場合、S は50語 sample を満たさない場合に短文用の
`null`/note schema を返す。これらの条件分岐も JSON 契約の一部である。

### Open English WordNet 2025

Open English WordNet（OEWN）から、token/type coverage、depth-eligible coverage、
token/type 加重の polysemy 平均、token/type 加重の hypernym-depth 平均を出力する。
polysemy 平均は OEWN に一致した lemma だけ、depth 平均はさらに noun/verb の depth を
持つ lemma だけを分母にするため、coverage と必ず併記する。

現行実装は POS を文脈から付与せず、語義曖昧性解消もしない。polysemy は辞書に載る
語義数、depth は記録された noun/verb hypernym path の長さであり、文中で使われた
意味の多さ、抽象度、文章の質、熟達度ではない。

### TUBELEX-EN Treebank

TUBELEX は専用 Treebank adapter で token 化し、次を token/type 加重で報告する。

- token/type coverage
- add-one smoothing を用いる `frequency_zipf_*_mean`
- Beta(1,1) smoothing を用いる `video_log10_prevalence_*_mean`
- Beta(1,1) smoothing を用いる `channel_log10_prevalence_*_mean`

未登録語も smoothing floor で平均に残し、coverage を別に示す。video/channel prevalence
は、混合的な YouTube 由来資料内でどの程度広く現れるかを表す。COCA、均衡コーパス、
会話能力、学習者 norm の尺度ではない。TUBELEX の video/channel prevalence は v1.0 に
含む一方、MASC/OANC を用いる将来の汎用 dispersion tuple や category entropy は含めない。

## 3. 権利確認済みランタイム資源

採用条件は、`data/resource_registry.json` で `tier=runtime-resource`、
`status.level=green`、`license.verified=true` であり、public SaaS 処理が許可されたことと
する。公開 v1.0 の resource ID は次の5個に固定する。

| registry ID | runtime selector | 配置 | v1.0 での扱い |
|---|---|---|---|
| `ngsl-1.2` | `ngsl` | bundled | 標準公開 inventory、`open_flemma` の根拠にも使用 |
| `nation-bnc-coca-headwords-10000` | `bnc_coca` | server-injected | 完全な public server-only gate が通った場合だけ |
| `nation-bnc-coca-families-25000` | `nation_bnc_coca_families` | server-injected | 完全な public server-only gate が通った場合だけ |
| `tubelex-en-treebank-7cb5fb36-frequency-index` | なし | bundled | TUBELEX 指標 |
| `open-english-wordnet-2025-metrics` | なし | bundled | semantic 指標と `open_flemma` の語彙根拠 |

NJ8 はこの公開 inventory に含めない。project owner の2026-07-22 attestation は証拠として
保持するが、original permission record、grantor authority、公開利用の全 scope、独立 review
が未確認であるため `yellow` / `review-pending` とする。CSV は current/future Git tree、release、
package、container、CI、公開 UI から除外し、`LDFREQ_SERVING_MODE=local` と
`LDFREQ_ALLOW_LOCAL_RESTRICTED=1` を同時に指定した operator-supplied local copy のみ扱う。
local runtime の `parenthetical_variant_expansion_v1` と
`surface_first_rank_lookup_normalized_fallback_v1` も permission review の transformation scope
に含める。

Nation 2資源が `green` であることは、raw list を Git、package、container、client へ
同梱してよいという意味ではない。これらは product policy により server-only とし、
既定では非表示にする。NGSL と server-only Nation を同じ「標準同梱」と表現しない。

完全な gate は、eligible ID の allow-list、権利 acknowledgement、固定 profile
`shared-abuse-controls-v1`、短い不透明な外部 evidence ID の4条件を要求する。evidence ID
は外部に保管した control review record への参照にすぎず、URL、file path、secret、
placeholder を入れてはならない。runtime は宣言値と ID の形だけを検査し、共有 rate
limiter、account quota、content-free audit、anomaly detection、extraction-resistance test
の存在や合格を検証しない。どれかが欠ける場合は、resource を表示、materialize、isolated
worker へ転送しない。

公開 UI の normalizer は `open_flemma`（既定）と `simplemma`（比較用）である。
AntBNC、legacy EAPFoundation、legacy TAALES data、利用者が持ち込む Range data はこの
公開 v1.0 resource inventory に含めない。

## 4. 出力 schema

### JSON

単一文書 payload の top-level key は次に固定する。

```text
ldfreq_version, output_schema_version, document, settings, method_notes, privacy,
n_tokens, n_types, panel_a, panel_a_records, panel_b, semantic_network, tubelex
```

`document.name` は入力ファイル名ではなく `Document 001` のような疑似ラベルである。
`panel_a` は既存 consumer のための scalar 投影、`panel_a_records` は計算状態・欠測理由・
method ID・要求/実効 parameter・独立した advisory quality 状態を持つ構造化正本である。
`settings` は normalizer/list の ID・版・lookup unit、Panel B mapping method ID、登録済み tokenizer policy ID、
threshold、segment/window/sample、seed、cutoff を記録する。tokenizer policy に自由記述
は許さず、記録した ID が実際の tokenization を駆動する。Panel B、OEWN、TUBELEX が明示的に
無効または利用不能なら、該当 section は `null` になりうるが key 自体は残す。

複数文書では `ldfreq_version`, `output_schema_version`, `batch`,
`batch_diagnostics`, `documents` を持ち、
diagnostics は `bands`, `reliability`, `overlap_matrix`, `overlap_pairs` に限定する。
全入力が tokenless 等で skip された場合だけ `batch_diagnostics` を省略する。入力本文、
元ファイル名、token 列、語ごとの head/rank、off-list 語行は保存・出力しない。

Panel B、OEWN、TUBELEX の全 nested key、短文時の分岐、`settings` key は機械可読契約を
正本とする。JSON の map にある integer key は、JSON serialization 後には文字列 key に
なる点に注意する。

公開JSONはUTF-8、`ensure_ascii=false`、2-space indent、末尾改行ありとする。有限floatは
小数点以下12桁へ丸め、`-0.0`は`0.0`、NaNは`null`にする。Infinityは数値異常を隠さず
exportを停止する。計算内部の精度は維持し、この丸めはJSONとExcelが共有する公開境界に
だけ適用する。

### Excel

常設 sheet は次の12枚である。

```text
summary, descriptives, panel_a, lfp, thresholds, p_lex_s,
p_lex_dist, s_empirical, band_wise, semantic_network, tubelex, metadata
```

batch の場合は `batch_bands`, `reliability`, `off_list`, `overlap_matrix`,
`overlap_pairs` を追加する。`off_list` は sheet 名の互換性のため予約するが、公開 privacy
契約上は空であり、将来 token/head 行をここへ戻すことは schema 追加ではなく privacy
境界の破壊として扱う。

`summary` 列と descriptive measure の順序は
[`v1-metric-scope.json`](v1-metric-scope.json) に固定する。summary は全詳細 key の複製
ではなく、主要な比較用 subset である。12個すべての Panel A 値は `panel_a` sheet と
JSON に残る。

XLSXはdocument propertiesの作成・更新時刻と全ZIP member時刻を1980-01-01へ固定し、
member名順、DEFLATE level 9で再梱包する。同一processだけでなくhash seedの異なる別process
でも同じbytesになることを検査する。release image digestをまだ固定していない段階では、
sheet順・寸法・全cell値のsemantic snapshotをplatform間の正本とし、XLSX binary SHA-256は
provisionalとする。image digest固定後に、その環境でbinary hashを正本へ昇格する。

canonical入力、JSON期待値、Excel semantic snapshot、各hashは
[`tests/fixtures/v1_golden/manifest.json`](../tests/fixtures/v1_golden/manifest.json) に固定する。
これはCC0のプロジェクト作成文2本であり、学習者作文でもLLM生成作文でもない。

## 5. 解釈契約

許される主張は、「記録された tokenizer、Panel B mapping method、normalizer、参照資源、
lookup unit、parameter
の下で測った、その提出テキストの記述的特徴」である。次の主張は禁止する。

- 評点、essay score、CEFR level の推定
- 語彙知識の診断、writer proficiency の直接推定
- writing quality の予測
- AI 生成・人間執筆の判定

テキスト間比較では、少なくとも tokenizer policy ID、Panel B mapping method ID、normalizer、
資源 ID/版、lookup unit、
parameter を一致させる。可能な限り、長さ、prompt、topic、genre、register、sampling
condition も揃える。高い/低い値は自動的に良い/悪いを意味しない。

特に、Panel B の off-list は「読者が知らない語」ではなく、選択語彙表と lookup policy
への不一致である。OEWN 平均は conditional mean であり、TUBELEX の rarity/prevalence
は topic、固有名、綴り、tokenization の影響も受ける。自動 warning の閾値は透明な
表示 heuristic であり、妥当性や信頼性の cut score ではない。

## 6. 明示的な非目標

次は v1.0 の metric/runtime scope に含めない。

- ELLIPSE の Vocabulary rating、総合評定、その他 rubric score の予測器
- ELLIPSE raw essay または fitted model の公開アプリ runtime への組込み
- CEFR classifier、grade/quality/proficiency regressor、composite score、AI detector
- MASC/OANC document frequency、汎用 contextual diversity/dispersion tuple、register
  entropy、n-gram association 指標
- TUBELEX category entropy
- POS-aware/contextual semantic 指標、word-sense disambiguation
- complete TAALES reconstruction、COCA-based TAALES 値との数値同等性
- permission-pending resource の公開利用

ELLIPSE は、将来の offline external-association study で記述指標と人手評定の関係を
検証するための fetch-only benchmark にはできる。ただし、その関連分析が成功しても、
評点予測 field/model を v1.x 製品へ自動昇格させない。

## 7. SemVer と変更管理

公開済み release は上書きせず、同じ入力・同じ release・同じ resource identity からの
再計算可能性を残す。`1.x` consumer は未知の追加 field を無視してよい一方、凍結した
field が存在し、その意味が変わらないことに依存できる。

全JSONとXLSX metadataは、アプリ実装を示す`ldfreq_version`と、出力構造・意味の互換性を
示す`output_schema_version`を別々に記録する。アプリのPATCH更新だけではschema版を変更
せず、schemaの非互換変更はアプリ版とは独立にMAJORを上げる。

### MAJOR を必要とする変更

- metric key、JSON field、Excel sheet、凍結列の削除・rename
- 式、分母、weighting、counting unit、tokenizer/lookup semantics、construct の変更
- type、nullability、nesting、意味の非互換変更
- 旧版を残さない resource identity の置換・削除
- 既存設定の結果を変える default 変更
- aggregate-only privacy 境界の弱体化

### MINOR で許される追加

- 既存挙動を保つ optional metric または namespaced field
- 凍結列を並べ替えず末尾へ加える Excel 列
- 旧 ID と default を残した、新しい `green` resource identity
- 非互換性のない method/interpretation card や warning

MINOR の前には、構成概念・式・資源・長さ感度・追加の解釈価値を記した card、権利・
provenance review、fixture/property test、schema と migration note を必須とする。

### PATCH で許される修正

schema と意図した metric semantics を変えない文書、引用、test、性能、packaging、security
修正を対象とする。凍結済みの式へ実装を戻す bug fix は PATCH にできるが、数値差を
release note に明示し、golden fixture を更新し、過去 release を改変しない。

## 8. 変更時のゲート

変更 PR は次を同時に満たさなければならない。

1. 実装、機械可読 JSON、本書を同じ変更で整合させる。
2. `pytest -q tests/test_v1_metric_scope.py` を通す。
3. 全 test と `scripts/check_public_release.py` を通す。
4. 新指標なら既存指標と異なる問いと追加の解釈価値を示す。
5. 新資源なら registry の `green` 判定、version/hash、provenance、配布形態を固定する。
6. ELLIPSE 等の evaluation benchmark と公開 runtime resource を同じ admission rule で
   扱わない。

この凍結により、v1.0 の最初の判定点は「指標をさらに増やせるか」ではなく、第三者が
新しい環境で権利確認済み資源だけを使い、同じ schema と数値を再生成できるかになる。
