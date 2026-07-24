# Open English WordNet attribution notice

This directory contains a derived data table based on **Open English WordNet
2025**, created by the Open English WordNet Community and derived
from Princeton WordNet.

- Source project: https://github.com/globalwordnet/english-wordnet
- Pinned release: https://github.com/globalwordnet/english-wordnet/releases/tag/2025-edition
- Source asset: https://github.com/globalwordnet/english-wordnet/releases/download/2025-edition/english-wordnet-2025.xml.gz
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)

Changes made by this project: lexical entries were normalized with Unicode NFKC
and case-folding, grouped by lemma and part of speech, and reduced to sense count
(polysemy) plus minimum/mean/maximum hypernym depth. Hypernym depth is the
longest path through `hypernym` and `instance_hypernym` relations to a root,
where root depth is zero. Definitions, examples, sense keys, and the source XML
are not included in the derived table. The CSV is sorted and gzip-compressed
deterministically.

No endorsement by the Open English WordNet Community is implied.
