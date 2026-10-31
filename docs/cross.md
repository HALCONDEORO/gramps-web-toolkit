# gwt-cross

Cross a downloaded name index against the people of the tree. Everything runs offline: it reads a CSV or TSV and a `gwt-check` dump, and prints the rows that agree. A score is a lead for the reader, never an identification.

```bash
gwt-check dump tree.json
gwt-cross index.csv --dump tree.json --only-parentless --min-score 6 --limit 30
```

Output (tab separated): `score`, Gramps ID, what agreed, the index reference, the row. A summary (`rows`, `people`, `leads`) goes to stderr.

| Option | Meaning |
| --- | --- |
| `--only-parentless` | only people with no parents in the tree, the ones the line stops at |
| `--min-score N` | default 6; surname alone scores 4 |
| `--limit N` | print the best N |
| `--format json` | the same rows as JSON |
| `--encoding E` | force the encoding; by default UTF-8 (with or without BOM), UTF-16 or Windows-1252, whichever decodes |

The index needs a header. Column names are matched in Spanish, French and English: surname (`apellido`, `nom`), given (`nombre`, `prenom`), year (`ano`, `annee`, a full date is fine), place (`lugar`, `lieu`, `commune`), reference (`ref`, `cote`, `url`). The delimiter (tab, semicolon, comma or bar) is detected.

Scoring: surname equal +4 (particles such as "de la" or a double surname +3, same sound after a rough key, so Vidal and Bidal or Gil and Jil, +3, close spelling +2); a shared given name +3 and the full given name equal +1 more; same place +2; year equal to a birth, baptism or death +3, within 5 +2, within 15 +1. A row with no surname agreement is dropped. A row that fits several people equally lists all their IDs, comma separated: a common name or a duplicate in the tree, never a pick. Rows are only compared with people sharing a surname sound letter, so 20,000 rows against thousands of people take a second or two.

Exit 0 when a row reached the score, 1 when none did, 2 when the index or the dump cannot be read.

Example: the row `DUPRE;Marie;1851;Saint Arnoult;R1` against Marie Anne Dupré, born 1850 in Saint-Arnoult, scores 11 (surname, given name, place, year).
