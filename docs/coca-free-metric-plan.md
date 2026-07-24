# COCA-free lexical-sophistication plan

Last reviewed: 2026-07-22

## Decision

The public service will not read, bundle, reconstruct, or require a licensed
COCA dataset. The project owner does not own COCA, and the legacy TAALES COCA
tables remain outside the application and public release.

This does **not** prevent lemmatization. COCA is a reference corpus, not a
lemmatizer. The public normalizer is the project-owned `open_flemma` algorithm,
which uses deterministic inflection rules and only cleared NGSL/Open English
WordNet resources.

The corpus-derived measures will be called **open reconstructions** or **new
metrics**, never `COCA_*`. Exact TAALES/COCA values and old component scores are
not reproducible without the original COCA inputs.

## What the legacy COCA layer supplied

The local TAALES 2.8 Index Guide contains 1,103 indices. A code-and-guide audit
identified 685 COCA-dependent variants (62.1%):

| Family | COCA-dependent variants |
| --- | ---: |
| Word frequency | 92 |
| Word range | 88 |
| Bigram/trigram frequency | 220 |
| Bigram/trigram range | 60 |
| Association strength | 225 |

These span academic, fiction, magazine, newspaper, and spoken registers, with
raw/lemma, token/type, and raw/log variants. Association measures include MI,
MI², t-score, directional delta-P, and approximate collexeme strength. The
large number therefore represents many parameterized variants of a smaller set
of constructs; it is not 685 independent theoretical dimensions.

The main loss is a single large, balanced, contemporary American-English
reference design. It affects numerical comparability, rare n-gram stability,
and the old COCA-based component scores. It does not affect Panel A diversity,
NJ8/NGSL/Nation list profiles, the new flemma normalizer, or Open English
WordNet metrics.

## Replacement axes

No open source is relabelled as COCA, and unlike corpora are not concatenated
into a pseudo-COCA. Each result retains its own corpus, snapshot, denominator,
tokenizer, normalizer, and coverage metadata.

| Axis | Proposed cleared source | Measures | Limitation/status |
| --- | --- | --- | --- |
| Everyday exposure | [TUBELEX-EN](https://github.com/naist-nlp/tubelex) published frequency lists | Treebank-unit smoothed Zipf frequency; video/channel log prevalence; explicit coverage | Admitted from a pinned, byte-reproducible aggregate table after the full test and public-inventory gates passed. Mixed global YouTube language is not balanced American conversation, and no source subtitles are bundled. |
| American multi-genre | [OANC](https://anc.org/data/oanc/) | lemma/surface frequency; document frequency; bi/trigram counts and associations | About 15M words and not register-balanced like COCA. Safe archive identity/acquisition remains pending. |
| Pipeline and genre validation | [MASC](https://anc.org/data/masc/) | reproducible unigram/document-frequency/bi/trigram fixtures; genre dispersion | About 500K words, so too small for stable rare n-grams. Offline aggregate builder is implemented; production artifact is pending verified acquisition. |
| General expository writing | [dated English Wikipedia dump](https://dumps.wikimedia.org/enwiki/) | article frequency/range, n-grams, register distinctiveness | Encyclopedic and global-English bias; CC BY-SA/GFDL conditions and snapshot notices must be retained. |
| Academic biomedical | [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/) filtered to approved licences | article frequency/range, academic specificity, n-grams | Must be named `academic-biomedical`; licence filtering is mandatory and the register is narrower than COCA Academic. |
| Printed language/fiction n-grams | [Google Books Ngram v3](https://storage.googleapis.com/books/ngrams/books/datasetsv3.html) | dated n-gram frequency and volume range | OCR, count censoring, and no full document context; separate metric family only. |

Raw OpenSubtitles text is excluded because the public corpus does not grant a
clear general right to redistribute the subtitle content. TUBELEX's published
aggregate lists and models are evaluated instead; its project explicitly says
the full subtitle text is not published for copyright reasons.

## Metric contract

The first open baseline will expose a compact set of constructs instead of all
685 legacy variants:

1. log frequency per million and Zipf frequency;
2. document/contextual diversity and a dispersion measure;
3. register specificity and normalized register entropy;
4. bigram/trigram log frequency and coverage;
5. MI, MI², t-score, log-likelihood, and directional delta-P where raw counts
   and compatible marginals exist; and
6. TUBELEX KenLM surprisal as an explicitly separate language-model measure,
   not as a replacement label for TAALES MI or t-score.

Every output must state the corpus/version, token count, document unit, surface
or project-flemma unit, smoothing/unseen policy, minimum n-gram count, and input
coverage. Register scores remain separate until a learner-writing validation
study justifies any combined score. Old TAALES component loadings will not be
reused.

### Implemented first axis: TUBELEX-EN Treebank

The pinned published asset is `tubelex-en-treebank.tsv.xz` at commit
`7cb5fb36add76b83a266d1967536e1a1d3faa513` (4,152,940 bytes; SHA-256
`4096022259d5eaa7261c3bf22c3b0af9fd58ae8eebe17894c0b34a163954f936`). The
declared corpus denominator is 171,805,865 tokens over 613,309 source
vocabulary rows, 105,733 videos, and 68,405 channels.

The input is independently retokenized after NFKC normalization, mapping common
typographic apostrophes such as `’` to ASCII, and lower-casing. The adapter then
uses deterministic sentence pre-segmentation and calls the model-free
`TreebankWordTokenizer` directly, so it requires no Punkt model at runtime.
TUBELEX used NLTK 3.8.1 for the source variant; production pins NLTK 3.10.0
after an official-source comparison confirmed compatible word-tokenizer rules.
The derived index contains only rows that this conservative adapter can emit;
coverage therefore remains an explicit result rather than being treated as
perfect.

For every adapted token/type, including an unseen item, frequency uses
`log10(10^9 * (count + 1) / (corpus_tokens + source_vocabulary_rows))`, the
add-one-smoothed Zipf scale. Video and channel dispersion use the log10
posterior mean prevalence under a Beta(1,1) prior, that is
`log10((range + 1) / (contexts + 2))`. Both token- and type-weighted means are
reported alongside token/type coverage. This avoids dropping unseen items from
the mean and avoids `log10(0)` for a zero range.

The upstream category counts are retained as provenance-compatible aggregates,
but raw plug-in category entropy is deliberately not exposed. It is strongly
sample-size biased for rare words; a later register statistic should use a
documented shrinkage distribution and Jensen–Shannon divergence, then establish
incremental validity out of sample. None of these values is labelled or
interpreted as COCA frequency, COCA range, or COCA register comparability.

## Implementation order

1. **Implemented:** public `open_flemma`; no COCA or AntBNC dependency.
2. **Implemented and admitted:** pinned TUBELEX-EN Treebank published
   aggregates, dedicated model-free tokenizer adapter, coverage, add-one Zipf,
   and Beta-smoothed video/channel prevalence. The 515,292-row production
   artifact is hash-pinned and byte-reproducible; the full test and
   public-inventory checks pass.
3. **Next:** admit the existing MASC aggregate build once official archive
   identity can be independently verified; then build OANC aggregates.
4. **Then:** add licence-filtered Wikipedia/PMC register artifacts and raw-count
   n-gram association modules.
5. **Then:** add contextual distinctiveness and co-occurrence-network measures,
   followed by out-of-sample validation against diversity, length, prompt, and
   existing frequency baselines.

Until an axis is implemented and admitted by the resource registry, the user
interface must not display a placeholder score for it.
