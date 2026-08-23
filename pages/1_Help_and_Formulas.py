"""Help / Formulas page — exact definitions for every index.

Notation used throughout:
  N      = number of tokens (running words)
  V      = number of types (distinct head forms, after the chosen unit)
  V(m)   = number of types occurring exactly m times  (the "frequency spectrum")
"""
import streamlit as st

st.set_page_config(page_title="Help & Formulas", layout="wide")
st.title("Help · Formulas & how to read the output")

st.markdown(r"""
This page gives the **exact formula** behind every number the analyzer reports.
Symbols: $N$ = tokens, $V$ = types, and $V(m)$ = the number of types that occur
**exactly $m$ times** (the *frequency spectrum*).

**How to read the Warning column** — each index has a recommended token floor for
stable interpretation. Texts or bands below that floor still report a value where
the formula permits it, but the Warning column marks the value as unstable
(cf. Kyle et al. 2024; Kojima & Yamashita 2014).

**Why a value can show “— (NA)”** — the value is mathematically undefined for this
text or procedure:
1. A perfectly diverse text gives
   no MTLD factor; an all-hapax text makes Yule's I undefined; vocd-D fails to
   converge.
2. Frequency-profile procedures such as S and P_Lex require their own minimum
   segment/sample size. These are genuine "no value", not a software error.
""")

# ---------------------------------------------------------------- Counting units
st.header("Counting units — token and reference lookup")
st.markdown("""
| View | Main question | Uses a frequency list? | Main unit |
|---|---|---:|---|
| **Panel A** | How varied or repetitive is the text's vocabulary? | No | lower-cased surface tokens |
| **Panel B** | How much of the text is covered by the selected reference list, and at what frequency bands? | Yes | hybrid surface-first list unit + normalized fallback |
| **Open corpus frequency** | How frequent and contextually widespread are the words in an open reference corpus? | TUBELEX-EN Treebank | lower-cased Treebank surface/clitic tokens |

Panel A and Panel B start from the same tokenized text, but they intentionally
summarize different constructs. Band-wise diversity in Panel B reuses Panel A-style
indices within each frequency band as a diagnostic view. The TUBELEX-EN Treebank view is a
separate reference-corpus axis; it does not change either panel or the Profile radar.
""")
st.markdown(r"""
The counting unit is fixed to **token**. Panel A therefore uses lower-cased
surface tokens: repeated occurrences contribute to $N$, and $V$ is counted over token
forms without lemmatization.

Panel B uses a **hybrid surface-first lookup**. It first tries the lower-cased
surface form as a key in the selected list. Only a miss is passed to the configured
normalizer and looked up again. NJ8 natively ranks POS-less spellings and listed
variants; NGSL supplies lemma ranks with inflected-form aliases; the Nation headword
files contain headwords only. For a rich BNC/COCA word-family list, a direct form key
maps to its **word-family head**. For AntWordProfiler/Range lists, unindented
basewords define families and indented entries are treated as family members.

NJ8 is absent from the public selector and release payload while its custom
permission assurance is `review-pending`. It can appear only when an operator
supplies a local copy and explicitly enables local restricted mode.

A **flemma** groups forms **regardless of part of speech** (McLean 2017/2018). The noun
*smoking* (“passive smoking”) and the verb *smoking* (“he is smoking”) are the **same**
flemma. This is the key contrast with a **lemma**, which groups inflections only
*within* one part of speech.

This tool's Panel B normalizers map a word form to a POS-agnostic head
**without using part-of-speech context**, so they cannot separate noun-*smoking*
from verb-*smoking*. That fallback resembles flemma normalization, but the full
Panel B method is not a pure flemma pipeline because direct list hits bypass it.
The public default is the project's
`open_flemma` algorithm: deterministic inflection rules plus the open NGSL 1.2 form
table, with rule candidates checked against Open English WordNet 2025. It does not
use AntBNC or COCA. When POS-less evidence permits more than one head (for example,
*saw* as a noun or the past of *see*), it keeps the surface spelling instead of
guessing. Simplemma remains available for comparison; AntBNC is local-only.

References: McLean (2018) *Evidence for the adoption of the flemma as an appropriate word
counting unit*, Applied Linguistics 39(6); Brown, Stoeckel, McLean & Stewart (2020).
""")

# ---------------------------------------------------------------- Panel A
st.header("Panel A — Lexical diversity (list-independent)")

st.subheader("Type–token ratios")
st.latex(r"\mathrm{TTR}=\frac{V}{N}\qquad "
         r"\mathrm{RTTR\ (Guiraud)}=\frac{V}{\sqrt{N}}\qquad "
         r"\mathrm{CTTR}=\frac{V}{\sqrt{2N}}")
st.latex(r"\mathrm{Herdan\ }C=\frac{\log V}{\log N}\qquad\qquad "
         r"\mathrm{Maas\ }a^{2}=\frac{\log N-\log V}{(\log N)^{2}}")
st.caption("Raw TTR falls as N grows (length dependence). Maas a² is **inverted**: "
           "a *smaller* value means *greater* diversity.")

st.subheader("Segment / window measures")
st.latex(r"\mathrm{MSTTR}=\frac{1}{S}\sum_{i=1}^{S}\mathrm{TTR}(\text{segment}_i)"
         r"\quad\text{(non-overlapping segments of length }L\text{)}")
st.latex(r"\mathrm{MATTR}=\frac{1}{N-W+1}\sum_{i=1}^{N-W+1}\mathrm{TTR}(\text{window}_i)"
         r"\quad\text{(moving window of length }W\text{, step 1)}")

st.subheader("MTLD (McCarthy & Jarvis 2010)")
st.markdown(r"""
Walk the tokens accumulating TTR; each time TTR drops to **≤ 0.72** that closes one
*factor* and the counter resets. The trailing run contributes a **partial factor**:
""")
st.latex(r"\text{partial}=\frac{1-\mathrm{TTR}_{\text{end}}}{1-0.72}\qquad "
         r"\mathrm{MTLD}_{\text{dir}}=\frac{N}{\#\text{factors}}")
st.latex(r"\mathrm{MTLD}=\tfrac12\bigl(\mathrm{MTLD}_{\text{forward}}+"
         r"\mathrm{MTLD}_{\text{backward}}\bigr)")
st.caption("A perfectly diverse text never closes a factor → MTLD is undefined (NA).")

st.subheader("HD-D (McCarthy & Jarvis 2007, hypergeometric)")
st.markdown(r"For each type $t$ with frequency $f_t$, the probability it appears at "
            r"least once in a random sample of $n$ tokens (default $n=42$):")
st.latex(r"P(t\in\text{sample})=1-\frac{\binom{N-f_t}{n}}{\binom{N}{n}}\qquad "
         r"\mathrm{HD\text{-}D}=\frac{1}{n}\sum_{t}P(t\in\text{sample})")
st.caption("HD-D is the expected TTR of a size-n sample (the analytic equivalent of "
           "vocd-D), bounded in [0, 1].")

st.subheader("vocd-D (Malvern & Richards 2002)")
st.markdown(r"Draw random samples of sizes 35–50, average their TTR, and fit $D$ to:")
st.latex(r"\mathrm{TTR}(n)=\frac{D}{n}\left(\sqrt{1+\frac{2n}{D}}-1\right)")
st.caption("Stochastic — a fixed seed makes it reproducible. If the mean TTR stays ~1 "
           "(near-maximal diversity) the fit does not converge and we return NA rather "
           "than a meaningless boundary value. Prefer HD-D for reproducibility.")

# ---------------------------------------------------------------- Yule (careful)
st.subheader("Yule's K and Yule's I — full derivation")
st.markdown(r"""
Both are built from the **frequency spectrum** $V(m)$ and its moments:
""")
st.latex(r"M_1=\sum_{m} m\,V(m)=N \qquad\qquad M_2=\sum_{m} m^{2}\,V(m)")
st.markdown("**Yule's K** (a repetition measure — *higher = more repetition = less diverse*):")
st.latex(r"K=10^{4}\cdot\frac{M_2-M_1}{M_1^{\,2}}")
st.markdown("**Yule's I** (*higher = more diverse*). Note the numerator is "
            "$V^{2}$ (types squared) and the denominator is $M_2-V$:")
st.latex(r"\boxed{\,I=\frac{V^{2}}{M_2-V}\,}"
         r"\qquad\text{(NOT }\;\frac{M_1^{2}}{M_2-M_1}\;\text{, the earlier error)}")
st.markdown(r"""
**Worked check** (the text used to validate the fix): $V=86$ types, and the spectrum
gives $M_2=\sum_m m^2 V(m)=260$. Then
""")
st.latex(r"I=\frac{86^{2}}{260-86}=\frac{7396}{174}=42.51")
st.caption("Yule's I is **undefined** when every type is a hapax "
           "($M_2=V\\Rightarrow$ division by zero) → reported as NA.")

st.subheader("Index profile radar")
st.markdown("""
The **Profile** view rescales selected Panel A and Panel B indicators to heuristic
0–100 scores. This makes the shape of a text easier to inspect, but it is **not**
a norm-referenced score and should not replace the raw index tables. The scaling
uses fixed caps for unbounded indices and inverts lower-is-more-diverse indices
(Maas, Yule's K). Short-text warnings and frequency-list effects still apply.

Current scaling rules:

- MATTR and HD-D are multiplied by 100.
- MTLD is capped at 150; vocd-D is capped at 100.
- **Low repetition** averages inverse Maas (cap = .20) and inverse Yule's K
  (cap = 1000).
- **Sophistication** averages Advanced Guiraud (cap = 5), % beyond K, and mean
  log-rank scaled by the selected list's maximum rank.
- **In-list coverage** is `100 - % off-list`.

These caps are visual guardrails, not published proficiency benchmarks.
""")

# ---------------------------------------------------------------- Panel B
st.header("Panel B — Frequency-based richness (selected reference list)")
st.markdown(r"""
Each token is first matched by lower-cased surface key. Only a direct-key miss is
normalized and looked up again; rich family entries return their family head. The
resulting selected-list unit is then assigned its **rank**; the band is
$K=\lfloor(\text{rank}-1)/1000\rfloor+1$ (K1, K2, …), or *off-list*.
""")
st.subheader("Tokenizer, proper nouns, and off-list policy")
st.markdown(r"""
New analyses use the fixed `english_unicode_v1` tokenizer policy. It applies NFC
(not NFKC), recognizes Unicode letters plus following combining marks, normalizes
common curly/modifier apostrophes to ASCII `'`, and retains apostrophes only inside
letter components. Numbers are not tokens; digits, hyphens, dashes, and periods split
tokens. For example, `well-known` becomes `well`, `known`, `abc123def` becomes
`abc`, `def`, and `U.S.A.` becomes `U`, `S`, `A`. Lower-casing uses Python
`str.lower()`, not case-folding. Exported provenance records the validated policy ID.
The former ASCII regular expression remains available only as the explicit
`ascii_legacy_v1` compatibility policy.

Proper nouns are **not automatically removed or credited as known**, and
capitalization is not used to make lookup easier. Public mapping diagnostics retain
only aggregate path counts/rates and type counts, never submitted terms or
token-to-head rows. If neither the direct surface key nor its normalized fallback is
in the selected list, the item is treated as **off-list**.

That is an implementation policy for selected-list matching, not a theoretical claim
that those words are unknown. Lexical-coverage studies normally include running words
such as proper nouns, marginal words, transparent compounds, and acronyms under an
explicit classification/known-word policy. This app does not perform that
classification automatically.

Unclassified off-list items remain in the denominator for selected-list coverage. They
therefore cap cumulative selected-list coverage, count as advanced/hard for Advanced
Guiraud, % beyond K, and P_Lex, and are included in the off-list percentage. Mean rank
excludes off-list items because they have no list rank.

Batch off-list diagnostics are heuristic surface-form labels (e.g. acronym/initialism,
capitalized form, contraction/possessive, derived/inflected form). They are review
aids, not named-entity recognition or part-of-speech tagging.
""")
st.subheader("Lexical Frequency Profile & coverage")
st.latex(r"\text{coverage}_K=\frac{\text{tokens in band }K}{N}\qquad "
         r"\text{cumulative}_K=\sum_{j\le K,\ j\in\text{list}}\text{coverage}_j")
st.markdown(r"""
**Coverage threshold** = the smallest selected-list band $K$ whose **cumulative
matched *text* coverage** reaches 90 / 95 / 98 % (lexical threshold; Laufer 1989;
Hu & Nation 2000). The denominator is the *total* token count, so unclassified
off-list items cap this selected-list match rate. If off-list items include proper
nouns or other normally creditable items, review/credit them according to your study
policy before interpreting the result as reader-known lexical coverage.

The aggregate profile reports selected-list bands, not a disclosed token-to-head
table. Results are not claimed to be numerically comparable to LexTutor. Such a
comparison would require the same hybrid lookup order, frequency list, word-family
expansion, tokenization, proper-noun/number policy, and normalizer.
""")
st.subheader("Advanced Guiraud, % beyond-K, mean rank")
st.latex(r"\mathrm{AG}=\frac{V_{\text{adv}}}{\sqrt{N}}\quad"
         r"(V_{\text{adv}}=\text{types beyond the cutoff band, incl. off-list})")
st.latex(r"\%\,\text{beyond-}K=100\cdot"
         r"\frac{\#\{\text{distinct heads in band}>K\ \text{or off-list}\}}"
         r"{\#\{\text{distinct heads in the text}\}}")
st.latex(r"\overline{\text{rank}}=\frac{1}{|\text{in-list}|}"
         r"\sum_{t\in\text{in-list}}\text{rank}(t)")
st.caption("Beyond-K is family/type-based in this implementation, approximated by the "
           "normalized head form available for the selected list. It is not token coverage.")

st.subheader("P_Lex and S")
st.markdown(r"""
**P_Lex (λ)** — Meara & Bell (2001). Split into consecutive 10-word segments; count
*hard* words per segment (hard = beyond the first 1000 / off-list). Fit the observed
distribution of per-segment hard-word counts to a Poisson and take the best-fit λ:
""")
st.latex(r"P(k)=\frac{\lambda^{k}e^{-\lambda}}{k!}\qquad "
         r"\hat\lambda=\arg\min_{\lambda}\sum_k\bigl(\widehat{P}_{\mathrm{obs}}(k)-P(k)\bigr)^2")
st.caption("Higher λ = richer. With 10-word segments λ is constrained to 0–10. Note: "
           "unclassified off-list items count as hard here under this app's "
           "no-automatic-proper-noun-adjustment policy. The fit uses all 0–10 "
           "hard-word categories, including zero-count categories.")
st.markdown(r"**S** — Kojima & Yamashita (2014, Eq. 4). Fit the coverage curve to the "
            r"text's cumulative coverage at ranks 500…3000 (50-token sliding window):")
st.latex(r"C(x)=\frac{\ln x}{\ln S}\times 100\qquad "
         r"\hat S=\arg\min_{S}\sum_{x}\Bigl(C_{\mathrm{emp}}(x)-\tfrac{\ln x}{\ln S}\cdot100\Bigr)^2")
st.caption("S = the frequency rank at which coverage is expected to reach 100% "
           "(higher = richer). ⚠ This app fits S on the selected list's ranks, not "
           "K&Y's BNC-spoken lists, so values are method-faithful but not comparable "
           "to published S (~2000–3500).")

# -------------------------------------------------------- Open corpus frequency
st.header("Open corpus frequency — TUBELEX-EN Treebank")
st.markdown(r"""
This view uses the published **TUBELEX-EN Treebank aggregate frequency table**, not
subtitle or transcript text. Matching first applies Unicode NFKC normalization and
maps common typographic apostrophes such as `’` to ASCII before lower-casing.
It then uses deterministic sentence pre-segmentation and the dedicated NLTK
Treebank surface/clitic tokenizer. For example, both `don't` and `don’t` become
`do` + `n't`, and `court's` becomes `court` + `'s`. TUBELEX used NLTK 3.8.1 for
the source variant; this service pins the audited compatible NLTK 3.10.0
word-tokenizer rules. It does not apply
part-of-speech tagging, Stanza lemmatization, `open_flemma`, Panel B word families, or
the selected frequency list. These corpus-specific lookup counts can therefore differ
from Panel A token/type counts.

The pinned reference contains $C=171{,}805{,}865$ tokens, $D_v=105{,}733$ videos,
$D_c=68{,}405$ channels, and $613{,}309$ source word rows. These values are also
recorded in the bundled manifest.

Let $f_d(w)$ be the number of occurrences of word $w$ in the input document,
$I_T(w)$ indicate that $w$ occurs in the TUBELEX-EN Treebank table, $N$ be all
TUBELEX-view input tokens, and $V$ be all TUBELEX-view input types. Let $C$, $D_v$,
and $D_c$ be the manifest's corpus-token, video, and channel totals. Matched token
and type coverage are:
""")
st.latex(
    r"\mathrm{coverage}_{token}=\frac{\sum_w f_d(w)I_T(w)}{N}"
    r"\qquad\mathrm{coverage}_{type}=\frac{\sum_w I_T(w)}{V}"
)
st.markdown(r"""
Let $V_T=613{,}309$ be the number of source word rows. Frequency uses add-one
smoothing and the conventional Zipf (log frequency per billion) scale:
""")
st.latex(
    r"Z(w)=\log_{10}\left(10^9\frac{c(w)+1}{C+V_T}\right)"
)
st.markdown(r"""
Contextual dispersion uses add-one-smoothed log prevalence. Let $v(w)$ and $q(w)$
be the numbers of videos and channels containing $w$:
""")
st.latex(
    r"P_v(w)=\log_{10}\frac{v(w)+1}{D_v+2}"
    r"\qquad P_c(w)=\log_{10}\frac{q(w)+1}{D_c+2}"
)
st.markdown(r"""
For all three measures, an out-of-vocabulary unit is assigned $c(w)=v(w)=q(w)=0$
instead of being removed. The resulting floors are $Z=0.7634$, $P_v=-5.0242$, and
$P_c=-4.8351$. The **token mean** weights every input lookup unit by $f_d(w)$; the
**type mean** gives every distinct input lookup unit equal weight. Thus, for any
smoothed per-word measure $g(w)$:
""")
st.latex(
    r"\overline g_{token}=\frac{\sum_w f_d(w)g(w)}{N}"
    r"\qquad\overline g_{type}=\frac{\sum_w g(w)}{V}"
)
st.markdown(r"""
Lower mean smoothed Zipf frequency means rarer vocabulary in this YouTube-derived
reference. Video/channel log prevalence closer to zero means the words occur across
more distinct contexts. These are **reference-relative descriptions**, not claims
that a text is automatically better or more proficient. Raw category entropy is
intentionally not reported in this first release because the 15 source categories
have very unequal base rates; a baseline-adjusted measure requires separate validation.

Always report coverage beside the means: unmatched names, spelling variants, and
malformed forms receive the smoothing floors and can lower both coverage and corpus
means without demonstrating sophistication. TUBELEX-EN Treebank does **not** use COCA
and should not be labelled a COCA
replacement or a spontaneous-conversation norm. It adds a large open, contemporary
exposure-frequency axis whose register must be stated explicitly.

The compact runtime artifact keeps only entries compatible with the app's lookup
token policy, while all rate denominators retain the full published TUBELEX-EN Treebank corpus
total. Excluded or out-of-vocabulary input forms therefore remain visible through
coverage rather than silently changing the reference denominator.

Resource: [TUBELEX repository](https://github.com/naist-nlp/tubelex) (BSD-3-Clause);
[TUBELEX paper](https://aclanthology.org/2025.coling-main.641/). The runtime artifact
contains derived aggregate rows only; the app does not bundle YouTube subtitle text.
""")

st.header("References")
st.markdown("""
- Covington & McFall (2010); Malvern & Richards (2002) — MATTR, vocd-D.
- McCarthy & Jarvis (2007, 2010) — HD-D, MTLD validation.
- Yule (1944); Herdan; Maas; Guiraud — classical indices.
- Laufer (1989); Hu & Nation (2000) — 95 % / 98 % lexical thresholds.
- Daller, van Hout & Treffers-Daller (2003) — Advanced Guiraud.
- Meara & Bell (2001) — P_Lex. Kojima & Yamashita (2014) — S.
- Kyle, Sung, Eguchi & Zenker (2024) — reliability of LD indices; Jarvis (2017).
- Nohejl et al. (2025) — TUBELEX multilingual YouTube frequency lists.
- AntBNC Lemma List — Laurence Anthony, https://www.laurenceanthony.net/software/antconc/
- New Word Level Checker (NWLC) — Mizumoto, https://mizumot.com/nwlc/
""")
st.info("Code: MIT · Bundled word-list and lemmatizer data is governed separately "
        "by the manifests under data/.")
