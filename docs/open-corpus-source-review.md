# Open corpus source review

Last checked: 2026-07-22

This note records the acquisition decision for the first open-corpus frequency
and n-gram artifacts. It separates a file being downloadable from that file
being suitable for a reproducible public build.

## Decision

Use the **MASC 3.0.0 data-only corpus** as the first raw-text pipeline input,
followed by OANC after the smaller build is validated. Separately, use the
already-published TUBELEX-EN Treebank frequency/contextual-diversity table as
the first large aggregate-only runtime axis. Its deterministic production
artifact has passed the full test suite, byte-for-byte rebuild checks, and the
public-inventory gate. Do not use the ANC Second Release frequency tables as an
OANC-derived resource.

MASC remains the first test of our own document-boundary and n-gram aggregation
code. TUBELEX does not change that role: its raw subtitles are not published,
and its published word table retains its upstream tokenization lineage and its
own metric names. See `coca-free-metric-plan.md` for the multi-axis design.

MASC is about 500,000 words and is suitable for testing document-frequency,
genre-dispersion, register-entropy, bigram, and trigram builds. It is too small
to serve as the sole reference for rare words or rare n-grams. The data-only
edition has no official lemma/POS layer, so any resulting lemma statistic must
be named `project-lemmatized`, not presented as an official MASC annotation.

## TUBELEX-EN aggregate identity and transformation

The selected official asset is
[`tubelex-en-treebank.tsv.xz`](https://raw.githubusercontent.com/naist-nlp/tubelex/7cb5fb36add76b83a266d1967536e1a1d3faa513/frequencies/tubelex-en-treebank.tsv.xz)
at commit `7cb5fb36add76b83a266d1967536e1a1d3faa513`:

- compressed size: 4,152,940 bytes;
- SHA-256: `4096022259d5eaa7261c3bf22c3b0af9fd58ae8eebe17894c0b34a163954f936`;
- declared tokens: 171,805,865;
- source vocabulary rows: 613,309;
- videos: 105,733; and
- channels: 68,405.

The [pinned repository license](https://github.com/naist-nlp/tubelex/blob/7cb5fb36add76b83a266d1967536e1a1d3faa513/LICENSE)
is BSD-3-Clause. The repository explicitly publishes frequency lists while
withholding full corpus text for copyright reasons. The derived runtime index
therefore starts only from this published table, preserves attribution and the
BSD notice, and records the filter and source identity in its manifest. It does
not fetch, parse, or bundle subtitles, video/channel IDs, titles, or document
names.

The runtime adapter applies NFKC, maps common typographic apostrophes such as
`’` to ASCII, lower-cases, and pre-segments sentences deterministically before
calling the model-free `TreebankWordTokenizer`; it does not download or load
Punkt. TUBELEX used NLTK 3.8.1 for the source variant, while production pins
NLTK 3.10.0 after confirming compatible word-tokenizer rules from the official
sources. Only aggregate rows that the conservative adapter can emit are copied
into the deterministically sorted and compressed server index; the original
corpus denominator and pre-filter source vocabulary size remain in provenance
metadata for smoothing and interpretation.

The first exposed measures are token/type coverage, add-one-smoothed Zipf
frequency including unseen units, and Beta(1,1)-smoothed log video/channel
prevalence. They are TUBELEX-specific everyday-exposure statistics and are not
COCA-comparable. Raw category entropy is deferred: the later register measure
will require shrinkage and Jensen–Shannon divergence rather than a biased
rare-count plug-in entropy.

## Official files considered

| Resource | Official distribution | Compressed bytes | Intended use |
| --- | --- | ---: | --- |
| MASC 3.0.0 data-only ZIP | [`masc_500k_texts.zip`](https://www.anc.org/MASC/download/masc_500k_texts.zip) | 1,351,682 | First deterministic build input |
| MASC 3.0.0 data-only TGZ | [`masc_500k_texts.tgz`](https://www.anc.org/MASC/download/masc_500k_texts.tgz) | 1,144,035 | Same content; ZIP preferred for streaming |
| MASC 3.0.0 full annotation ZIP | [`MASC-3.0.0.zip`](https://www.anc.org/MASC/download/MASC-3.0.0.zip) | 36,358,521 | Later POS/lemma and sentence-boundary validation |
| OANC GrAF ZIP | [`OANC_GrAF.zip`](https://www.anc.org/OANC/OANC_GrAF.zip) | 655,230,430 | Second-stage reference corpus |
| TUBELEX-EN Treebank aggregate | [`tubelex-en-treebank.tsv.xz`](https://raw.githubusercontent.com/naist-nlp/tubelex/7cb5fb36add76b83a266d1967536e1a1d3faa513/frequencies/tubelex-en-treebank.tsv.xz) | 4,152,940 | Admitted large aggregate-only axis; derived index is hash-pinned and byte-reproducible |

The [MASC project page](https://anc.org/data/masc/) identifies MASC as CC BY
3.0 US. A distributed derivative must retain attribution, link the license,
identify the frequency/n-gram transformation, and separate the data artifact's
license from the repository's MIT-licensed code.

The [OANC project page](https://anc.org/data/oanc/) describes use and
redistribution as unrestricted, but the applicable
[LDC Open Portion license](https://catalog.ldc.upenn.edu/license/anc-2nd-release-open.pdf)
still imposes notice, attribution, and modification-record requirements. OANC
must therefore not be described as CC0 or public domain.

## Excluded shortcut

The downloadable tables on the
[ANC Second Release frequency page](https://anc.org/data/anc-second-release/frequency-data/)
were computed over the 22,164,985-token Second Release, not just OANC. That
release includes materials governed by an LDC license. Those tables remain
`yellow` and must not be bundled as an open OANC frequency resource.

The OANC site exposes an interactive n-gram search interface, but no current
bulk bigram file or documented reproducible API was found. Required n-grams
will therefore be built from the admitted corpus text rather than scraped from
the query service.

## Safe acquisition gate

On 2026-07-22, direct access to the ANC archive host failed ordinary TLS
certificate validation, and no official SHA-256 was published for the archives.
The fetch script must never disable certificate validation. Acquisition can
proceed only after one of these conditions is met:

1. ANC repairs the certificate;
2. ANC publishes a secure official mirror and archive checksum;
3. ANC directly confirms the archive SHA-256 through a verifiable channel; or
4. another official distribution channel is identified and its terms reviewed.

The offline builder is now implemented in
`scripts/build_masc_aggregates.py`. It accepts only a user-supplied local ZIP,
requires a pinned expected SHA-256 and acquisition date at the command line,
and performs no network retrieval or TLS override:

```bash
python3 scripts/build_masc_aggregates.py \
  --source /path/to/masc_500k_texts.zip \
  --expected-source-sha256 <independently-confirmed-sha256> \
  --acquired-on YYYY-MM-DD
```

The builder reads UTF-8 `.txt` members without extracting the corpus, resets
n-gram state at document boundaries, and emits only deterministic surface
unigram/document-frequency, bigram, and trigram CSV.gz artifacts plus a
manifest and NOTICE. It does not copy source text or member names. The manifest
records source and artifact SHA-256, counts, tokenization, transformations, and
the acquisition date.

The builder verifies that a supplied archive matches the expected digest; it
cannot establish where the digest came from. A hash calculated only from the
same local download is reproducibility metadata, not independent provenance.
The resulting artifacts must therefore remain outside the green public build
until one of the acquisition conditions above supplies trustworthy identity
evidence.
