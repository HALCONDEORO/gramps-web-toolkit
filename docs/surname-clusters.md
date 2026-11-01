# Surname clusters

`gwt surname-clusters CONFIG ALIAS` and `gwt-surname-clusters CONFIG ALIAS` read a local UTF-8 JSON file and print JSON. They never fetch records or change the tree, ledger or configuration. Gramps is the source of truth; an old workbook is only an archived backup.

```json
{"clusters": [{"cluster_id": "invented-A", "aliases": [{"spelling": "Alfa", "provenance": "invented register A, view 1"}, {"spelling": "Alpha", "provenance": "human-reviewed invented note"}]}]}
```

```sh
gwt surname-clusters invented.json Alfa
gwt-surname-clusters invented.json Alfa
```

Each literal alias needs provenance. Cluster IDs are explicit human choices, not automatically inferred families. Only case/accent folding is used. When an alias belongs to several clusters, all candidates are retained, no flattened suggestions are returned, and exit status is 1. Matched and unmatched inputs exit 0. Invalid configuration or input exits 2. Duplicate JSON keys, nonfinite constants and duplicate normalized aliases within one cluster are refused. Cross-cluster overlap is allowed and stays ambiguous.

Every result requires human review. This is fixed offline lookup, not phonetics, automatic clustering, identity proof or a merge plan. Variants and catalogue integration are deferred.

Text fields and the input alias must contain valid Unicode characters; lone surrogate escapes in JSON are rejected with exit status 2 before output.
