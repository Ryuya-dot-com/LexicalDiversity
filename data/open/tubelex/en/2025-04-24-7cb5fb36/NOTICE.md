# TUBELEX attribution notice

The derived frequency index in this directory is based solely on an already
published **TUBELEX** frequency table from the NAIST Natural Language Processing
Laboratory project.

- Project: https://github.com/naist-nlp/tubelex
- Source version: `7cb5fb36add76b83a266d1967536e1a1d3faa513`
- Published frequency table: https://raw.githubusercontent.com/naist-nlp/tubelex/7cb5fb36add76b83a266d1967536e1a1d3faa513/frequencies/tubelex-en-treebank.tsv.xz
- Source repository license: [BSD 3-Clause License](https://github.com/naist-nlp/tubelex/blob/7cb5fb36add76b83a266d1967536e1a1d3faa513/LICENSE)
- Citation: Adam Nohejl et al. (2025), *Beyond Film Subtitles: Is YouTube the
  Best Approximation of Spoken Vocabulary?*, COLING 2025,
  https://aclanthology.org/2025.coling-main.641/

Changes made by this project: the published TSV table was schema-checked,
integer and total invariants were verified, and only keys that can be emitted by
the pinned Unicode-alphabetic Treebank adapter were retained (TUBELEX English Penn Treebank tokens after NFKC and lower, restricted to Unicode-alphabetic lexical components and at most 64 code points).
Retained rows were sorted by the exact word field, converted to CSV, and
gzip-compressed deterministically. Counts on retained rows and the original
corpus total row were preserved.

Submitted text is matched with a separate runtime adapter: Unicode NFKC,
normalization of common typographic apostrophes to ASCII, lower-casing,
deterministic sentence pre-segmentation, and model-free Treebank word-tokenizer
rules. TUBELEX used NLTK 3.8.1 for the source variant;
this service pins NLTK 3.10.0, whose word-tokenizer
rules were audited as compatible, and downloads no NLTK data model.

No source subtitle document, contiguous subtitle passage, subtitle filename,
video ID, channel ID, video title, source document name, or local input path is
present in the derived index or its manifest. Retained published frequency keys
are limited by the lexical predicate described above. The repository's software
license does not replace the license of the TUBELEX source. No endorsement by
the TUBELEX authors or NAIST is implied.

## Upstream BSD 3-Clause License

BSD 3-Clause License

Copyright (c) 2022-4, Adam Nohejl
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
