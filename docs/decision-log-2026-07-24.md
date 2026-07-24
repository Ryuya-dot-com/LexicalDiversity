# 意思決定ログ — 2026-07-24

対象期間: 直近30日
北極星: 公開ツールv1.0を、第三者が同じコード・依存・資源から再計算できる状態にする。

## 現在地

- ELLIPSEは権利・identity・schema・privacy境界・確認的分析計画まで固定済みだが、
  指標と人手評定の関連は未計算である。
- defaultのローカル環境はPython 3.14.3、NLTK 3.9.4を含む複数の依存driftがあり、
  runtime identity checkが意図どおり停止する。別のPython 3.12.10 clean venvでは、46個の
  exact runtime pin、NLTK 3.10.0、`pip check`、全pytest（263 passed, 2 skipped,
  6 subtests）、release
  gateが通った。ただし現在のworktreeは広い未コミット変更を含み、GitHub hosted CIは
  未実走なので、remote release evidenceはまだない。
- 未コミット変更が広いため、新規機能を増やすより、公開範囲と再現環境を固定する方が
  90日以内の公開可能性を大きく改善する。
- GitHub originは既にpublicで、現行treeから除外中のpermission-pending payloadが
  過去commitから到達可能である。削除commitだけでは公開履歴の境界は修復されない。

## 今回の判断

| ID | 判断 | 大局的な理由 | 可逆性・停止条件 |
|---|---|---|---|
| D1 | CPython 3.12.10とwheel hash lockを使うCIを最優先する | 著者環境だけで通る状態を解消し、依存driftを公開前に検出する | CI設計は可逆。監査済み依存を緩めて通す変更は禁止 |
| D2 | v1.0の指標key・資源・出力schema・非目標を機械可読に凍結する | 指標追加によるscope creepと、後のsilent schema driftを防ぐ | 追加は原則minor、削除・意味変更はmajor。v1.0前の変更も理由を記録 |
| D3 | Synthetic pilotはprotocolだけを固定し、今回は生成しない | 費用、provider terms、model driftを確認前に不可逆なdatasetを作らない | 生成14日前の公式preflightと費用見積が通らなければ延期 |
| D4 | ELLIPSEのoutcome analysisをまだ実行しない | clean CIとscope freeze前に結果を見ると、実装変更と分析判断が混ざる | 事前gateをすべて通した後の別runに限定 |
| D5 | 新規コーパス・新規指標・評点予測機能を今期追加しない | 既存資産だけでv1.0の公開価値が成立し、追加は保守面を増やす | 既存資産で答えられない事前登録済みの問いが出た場合だけ再審査 |
| D6 | アプリ版と公開出力schema版を分離する | コードのPATCH更新と出力互換性を混同せず、各出力を生成版へ遡及可能にする | `ldfreq/release.json`だけを正本とし、tag releaseはclean tree・dated changelog・annotated exact tagを必須にする |
| D7 | release判定を最新treeから到達可能Git履歴まで拡張する | 既に公開された旧blobは削除commit後もclone可能であり、treeだけの検査では資源境界を保証できない | 新しいclean-history public repoを第一候補とする。visibility変更、history rewrite、object pruneは別の明示承認まで実行しない |

## 判断を反映した契約

- D1: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)、
  [`requirements-ci.txt`](../requirements-ci.txt)、
  [`scripts/check_runtime_environment.py`](../scripts/check_runtime_environment.py)
  を追加した。さらにCPython 3.12.10、51個のLinux x86_64 wheel SHA-256、46個の
  production wheel SHA-256、Python base imageのlinux/amd64 child manifestとconfigを
  固定した。workflowは実装済みだが、remote clean checkoutでの成功はまだ観測して
  いないため、CI完了とは扱わない。
- D2: [`v1-metric-scope.json`](v1-metric-scope.json) と
  [`v1-scope-freeze.md`](v1-scope-freeze.md) に、現行指標、runtime資源、JSON/Excel schema、
  解釈境界、非目標、SemVerを固定した。実コードとの不一致は
  [`test_v1_metric_scope.py`](../tests/test_v1_metric_scope.py) で停止する。
- D2: [`tests/fixtures/v1_golden/manifest.json`](../tests/fixtures/v1_golden/manifest.json)
  にCC0のcanonical入力2本、実NGSL/OEWN/TUBELEXから得たJSON期待値、Excel全cell snapshot、
  hashを固定した。公開floatは小数点以下12桁へ丸め、XLSXのdocument/ZIP時刻とmember順を
  正規化した。binary XLSX hashはexact image digest固定までprovisionalとする。
- D3: [`pilot-protocol.json`](../benchmarks/synthetic/pilot-protocol.json) と
  [`synthetic-pilot-protocol.md`](synthetic-pilot-protocol.md) に48作文pilotを事前固定した。
  状態は `specified_not_executed`、API呼出し0回、費用0円である。6,000円または
  10,000円と同額になる次のrequestも送らず、4,000円留保による自動再開を認めない。
- D3/D4: 公開inventoryはELLIPSEのreview済みmetadata 2ファイルとSynthetic protocol
  1ファイルだけを許す。人間作文、生成作文、API response、attempt、backupは別の公開
  審査までGit追跡を拒否する。
- D5: 旧ロードマップに残っていたqmdの汎用dispersion tuple本実装をv1.0前の工程から
  外した。TUBELEX video/channel prevalenceは現行v1 scopeに残すが、MASC/OANC汎用
  dispersion、register/category entropy、新しいassociation指標は同じものとして扱わない。
- D6: [`ldfreq/release.json`](../ldfreq/release.json) をapplication/output schema版の
  単一正本とし、全export、golden manifest、CHANGELOG、CI、annotated tag gateへ接続した。
  現在は`0.9.0-dev.0`であり、tag 0件・dirty worktreeのためrelease modeは意図どおり停止する。
- D7: [`check_git_history.py`](../scripts/check_git_history.py) は22 reachable commits・
  62 unique pathsを検査し、現状44件でreleaseを停止する。このうち明示的なyellow artifactは
  AntBNC 1件とEAPFoundation BNC/COCA 2件である。その他のgreen/server-only・legacy pathと
  同じ権利状態だとは推定せず、移行判断は
  [`public-history-migration.md`](public-history-migration.md)へ分離した。また現在のindexにある
  15 `AD`、13 `AM`、6 `MM` pathは
  [`check_staging_coherence.py`](../scripts/check_staging_coherence.py)でcommit前に停止する。
  [`build_clean_public_candidate.py`](../scripts/build_clean_public_candidate.py)はmixed indexを
  commitせず、既存かつnon-ignoredなreview対象だけを決定的tarとcanonical evidenceへ変換する。
  server-only Nation artifactを必要とするStreamlit統合試験は明示的opt-inとし、通常suiteの
  権利gate検証はlocal private payloadから独立させた。

## 90日価値の確認

今回の三成果物—CI、v1.0 scope contract、Synthetic pilot protocol—は、それぞれ
公開可能性、後方互換性、将来の分析再現性を直接改善する。いずれも人間作文を外部へ
送らず、API費用を発生させず、公開アプリの入力・出力を拡張しない。

一方、次は今期のcritical pathから外す。

- ELLIPSEの評点関連推定とfitted model作成。
- PELIC、PERSUADE、ICNALE、OANC、MASCの追加取得・統合。
- Cloud Run本番化、匿名実作文SaaS、TAALES全指標の再実装。
- API modelや価格を現時点の候補だけで固定すること。

## 次の判定点

次へ進む条件は、clean CIが固定依存で全testとrelease gateを通し、v1 scope contractが
実コードと一致し、Synthetic pilot protocolが費用・provenance・公開境界を機械的に
検証できることである。その後も、最初の公開判定は論文や相関の有無ではなく、cleanな
第三者環境でv1.0候補を再構築できるかで行う。

canonical golden fixturesとJSON/Excel serialization regressionはローカルで完了した。
package wheel hashとexact Python base-image manifestもローカルで固定した。
残る順序は、(1) public historyの移行方式と既存originのvisibilityを決定、
(2) clean-history checkoutでhosted CIを実走、(3) application imageを構築して
生成digest・provenance・scanとgolden再現を記録、(4) v0.9 candidateを再構築、
(5) release文書・CITATION・archiveを整備、
とする。Synthetic生成とELLIPSEの評定関連分析はこの列へ割り込ませない。
