# Benchmark resource audit (2026-07-23)

This audit records what is actually present in the local source archives added
for the validation roadmap. It distinguishes a downloadable repository archive
from an analysis-ready data payload. Learner texts and row-level demographic
data were inspected only locally and are not reproduced here.

Audit environment: Python 3.14.3, pandas 2.3.3, Info-ZIP `unzip`, Poppler, and
visual rendering of the relevant PDF/DOCX pages. All source archives passed
archive-integrity checks. The entire `LexicalSophistication/` directory is
excluded by the repository `.gitignore` and none of these payloads is tracked.

## Decision summary

| Resource | What the local archive contains | Analysis-ready now | Roadmap decision |
|---|---|---|---|
| **ELLIPSE** | Final train/test essays and averaged scores, raw rater file, rubric, README | **Yes** | Primary Layer 3 external criterion; process locally and publish aggregate results only |
| **PELIC v1.1 repository ZIP** | Documentation, notebooks, images, and eight Git LFS pointer files | **No** | Optional post-v1 longitudinal/known-groups replication after verified LFS payload acquisition |
| **PERSUADE 2.0 repository ZIP** | README and three scoring rubrics; data are linked externally | **No** | Optional robustness arm after external CSV acquisition; not a vocabulary-rating criterion |
| **ELLIPSE preprint** | Complete 30-page preprint | **Yes, as documentation** | Use for methods and limitations; use final publisher metadata for citation |

The practical consequence is that ELLIPSE can support the planned validation
without waiting for a newly collected human corpus. PELIC and PERSUADE are not
on the v1.0 critical path because the files currently present are not their data
payloads.

## ELLIPSE

Official source: <https://github.com/scrosseye/ELLIPSE-Corpus>
Repository archive comment/commit: `dc3b8f0b3b4332fc9f64302c4ccfc4ed582f4b43`
License evidence: the README states CC BY-NC-SA 4.0; there is no separate
`LICENSE` file in the archive.

### Final data audit

| Check | Result |
|---|---:|
| Train rows / columns | 3,911 / 26 |
| Test rows / columns | 2,571 / 26 |
| Combined essays | **6,482** |
| Unique essay IDs | **6,482**; no train/test ID overlap |
| Exact duplicate essay texts across final data | 0 |
| Missing cells | 1 (`SES` in test); no missing analytic or holistic scores |
| Prompt labels | **44** in train, 44 in test, and 44 combined |
| Task labels | One: `Independent` |
| Scores | Overall plus Cohesion, Syntax, Vocabulary, Phraseology, Grammar, Conventions |
| Score range | 1.0-5.0 in 0.5 increments after averaging two ratings |
| Vocabulary mean | 3.2309 |
| Prompt sizes | 38-489 essays; mean 147.318 |
| `num_words3` | 14-1,274; mean 427.793 |

All 44 prompts occur in both provided splits. The supplied train/test division
therefore cannot test generalization to unseen prompts. Confirmatory predictive
analyses must use prompt-grouped resampling or a held-out-prompt design.

The raw-rater CSV contains 8,890 rows and 21 columns. Exactly 2,408 rows lack a
final `text_id_kaggle`, matching the number of essays removed after the reported
reliability pruning. It should not be used to reconstruct the final scores:

- 0 occurs in raw rating columns although the normal rubric is 1-5;
- only 6,468 of the 6,482 final IDs match the raw file exactly, leaving 14
  unmatched IDs on each side; and
- for the common IDs, 70 Vocabulary values differ between the final value and a
  naive mean of the two raw columns, consistent with adjudication or file-version
  differences.

The final train/test CSVs are therefore the primary analysis source. The raw
file is a secondary reliability resource only after the 14 ID discrepancies and
the coding of zero have been resolved explicitly.

### Rating evidence and limitations

The rubric defines Vocabulary independently from Phraseology and the holistic
score. Its descriptors combine lexical range, precision, topic-related terms,
less-common words, word choice, and word formation. This is related to lexical
sophistication but is not an error-free gold standard.

The preprint reports pre-adjudication Cohen's kappa of `.518` for Vocabulary
(PDF p. 12). The final Many-Facet Rasch analysis reports essay-ability
reliability of `.94` after removing 2,408 essays (p. 14), but that statistic is
not a Vocabulary-specific inter-rater reliability coefficient. Both values must
be reported rather than using `.94` to characterize the Vocabulary rating.

There is also a source-document inconsistency:

- the abstract and introduction say **29 prompts** (pp. 1 and 5);
- the corpus-development section says **44 prompts** (p. 9);
- the released CSV has 44 prompt labels; and
- the reported mean of 147.318 essays per prompt (p. 15) equals
  `6,482 / 44`, while Figure 3 (p. 16) displays 44 prompts.

The released data and internally consistent calculations support **44** as the
operational prompt count. Any report should state this discrepancy explicitly.

### ELLIPSE identities

| File | SHA-256 |
|---|---|
| `ELLIPSE-Corpus-main.zip` | `1e0953e04cbde26fa0693ef0772bb6c4db17d006301c35bc81eb392733183cbb` |
| `ellipse_pre_print.pdf` | `9f1560bde1f6db7bf3a336112bb954bb3949064cb1bbe4c2d7e3de209046045b` |
| `ELLIPSE_Final_github_train.csv` | `782344e99668a3ff508d7410c0eb6e36da70f3b28f81c96e367f1ca04924b06c` |
| `ELLIPSE_Final_github_test.zip` | `9ecddfcda83f6a99c8a24dda47fb1c30673fedeaf578a0fadac6645fd151a2f8` |
| decrypted `ELLIPSE_Final_github_test.csv` | `7e990c6392a9df9554d15bdd22f0b568d19095cd6676ad39cb1eaa69c977ed7a` |
| `ELL_Rubrics.docx` | `0c0ac5dfa6ae89c9c99ffe483c13ea4a57e8152a1472797e1fe361c2c203efdc` |
| `README.md` | `181fe4d9d6ebbce4a64de2a56f1a0afc9df19989e453dcf7f36db55614da0fed` |
| `ellipsis_raw_rater_scores_anon_all_essay.zip` | `f9c3cede2d54144225e0651af6415137f7da07f2d2955e8f8587896c293d5e85` |
| decrypted raw-rater CSV | `6972b8a960fc9f1986046aacef18792438bf90326ec55a89272932453e2f519b` |

The password-protected test and raw archives passed integrity checks with the
passwords documented in the upstream README. Passwords belong in a fetch
adapter or operator instructions, not in a public manifest containing data.

## PELIC

Official source: <https://github.com/ELI-Data-Mining-Group/PELIC-dataset>
Repository archive comment/commit: `c4526baeb8fb5d69732f9e2a8e1430b41ed38c53`
License evidence: the README states CC BY-NC-ND 4.0; there is no separate
`LICENSE` file in the archive.

The ZIP is intact, but each CSV is a 130-134 byte Git LFS pointer rather than
the data. For example, `PELIC_compiled.csv` points to a 182,138,668-byte object;
the local file is only 134 bytes. Row counts, schemas, missingness, and key
integrity therefore cannot yet be checked against the payload.

| Expected LFS object | Expected bytes | LFS SHA-256/OID |
|---|---:|---|
| `PELIC_compiled.csv` | 182,138,668 | `96652190aa83720c4d3214f86aa4858faed59cf9245be4491eb312631c297dab` |
| `answer.csv` | 181,598,254 | `32ceeb6c23c1b3adc601fe1a664365daa35e539120b1f8614e14104325abb54e` |
| `course.csv` | 22,779 | `d6b710cd284126383f96dd9fccc088ba3dcb28b5e80349cde531d565254c22c2` |
| `question.csv` | 651,736 | `0b789694740ffae6620bb3f3e65d3ed18dcde3278fa1de5d821fbcaf5873e5cf` |
| `student_information.csv` | 309,018 | `aed51c29dcc1037a87470a31ec17ec0c244747572a9c1f13742c648575500416` |
| `test_scores.csv` | 55,503 | `2cfab62f80ee1f0789f8adc38f913b7c89722315e62ca277adf441ccaed4f104` |
| `lemma_frequencies.csv` | 849,843 | `575a3e81c484a2821ca6221ef10eaa693a9d72eeb03dfe2d5efb1fabfad8b648` |
| `word_frequencies.csv` | 1,020,556 | `aaf3d1ddd54012efe51fbbadd1c0593624be55cb35ba68c73e48116fc2d184e7` |

Before use, acquire the LFS objects from a pinned release or commit and verify
each payload against its pointer. At minimum, `PELIC_compiled.csv` plus
`question.csv` is needed to retain prompt/task information; a relational audit
should also test primary keys, foreign keys, duplicates, and the README's
reported 46,230 texts. PELIC remains optional because it has course levels and
longitudinal information but no independent essay-level Vocabulary rating.

Archive SHA-256:
`659915762cb6d1771fa7af1673cf2715da3876f3ec060a7e44016535b4cb8cac`.

## PERSUADE 2.0

Official source: <https://github.com/scrosseye/persuade_corpus_2.0>
Repository archive comment/commit: `67d182ac88ea4a4dda736de859cfdb0bc360ee9b`
License evidence: the README states CC BY-NC-SA 4.0; there is no separate
`LICENSE` file in the archive.

The local ZIP contains no CSV, essay text, score table, data dictionary, prompt
set, or source materials. It contains a README and three rubrics only. The
README links to external training and password-protected test data on Google
Drive. Those payloads were not part of this local audit.

The holistic 1-6 scores and discourse-element effectiveness ratings are useful
for optional writing-quality or argumentation robustness checks, but neither is
a Vocabulary analytic rating. PERSUADE must not replace ELLIPSE as the primary
external lexical criterion.

| File | SHA-256 |
|---|---|
| `persuade_corpus_2.0-main.zip` | `707eeb9a2c22e70a6589d7f01295848279ce670d8d68c099207cdaf16eb61cad` |
| `README.md` | `8087d61fb68aeab316232c4478e9bad2dd1af23b65df2f2bd68bc95582f62f7e` |
| argumentation-effectiveness rubric | `01c5f7be693c0e659ac23b2883afb3ca84d35f66a30334fa0bcfc1a370a72d75` |
| independent-writing rubric | `3a7df1b054e50d5ecdcb56f3e0b37d6f5f244612e5f442e04d4ae2ee5d1abcc3` |
| source-based-writing rubric | `02ef3c1bfe4f19d105deea9c776614a10fae066deaa3d110fc7a28c546c43da5` |

## Gates created by this audit

Implementation checkpoint (2026-07-23): gates 1, 3, and 5 are enforced by the
public [`ELLIPSE manifest`](../benchmarks/ellipse/manifest.json), the separated
network-acquisition/offline-content-verification workflow in
[`fetch_ellipse.py`](../scripts/fetch_ellipse.py), the frozen
[`analysis plan`](../benchmarks/ellipse/analysis-plan.json), and the public
release tests. Gates 2 and 4 are fixed in that plan but remain unexecuted; no
tool-metric/human-rating association has yet been calculated.

1. Freeze the ELLIPSE confirmatory analysis protocol before calculating any
   association between tool indices and human ratings.
2. Use the final ELLIPSE CSVs as the primary criterion source; treat raw ratings
   as a secondary reliability analysis after resolving ID/coding discrepancies.
3. Pin the outer archive, upstream commit, nested archive, decrypted CSV, README,
   rubric, acquisition date, and license evidence in a fetch/verify manifest.
4. Use prompt-grouped resampling and prompt-clustered uncertainty; report the
   Vocabulary kappa limitation and do not call the rating a gold standard.
5. Keep every human essay and individual attribute outside Git, packages,
   containers, CI artifacts, application state, and external LLM APIs.
6. Do not acquire PELIC or PERSUADE merely to increase corpus count. Reconsider
   them only after v1.0 or when they answer a preregistered question that
   ELLIPSE and the synthetic benchmark cannot answer.
