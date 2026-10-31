# gwt-access

A small map of how each archive can be read, so no lane rediscovers it. One TSV row per archive: kind, viewer (`js`, `pdf`, `images`, `index-only`, `none`), captcha, download, name index, whether they answer a letter, URL, date checked, note, alias (other names for the same archive, `|` between them; `set --alias`). `show`, `plan` and `corpus --into` find a row by its name or an alias, so the open data file name and a short name do not make two rows.

```bash
gwt-access --map access.tsv set "AD41" --kind departmental --viewer js --captcha no --index yes
gwt-access --map access.tsv show AD41
gwt-access --map access.tsv list --captcha no
gwt-access --map access.tsv plan "AD41,AD37,Boulogne"
```

`plan` prints one verdict per archive: read now (viewer and no captcha), check (captcha not recorded), write for the act, blocked (needs a person), or unknown (never looked at). `set` changes only the fields given and stamps the date. The map path can also come from `GWT_ACCESS`.

Exit 0 on success, 1 when `show` finds no row, 2 on a bad argument.
