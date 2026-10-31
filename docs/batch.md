# gwt-batch

Turns the open tasks of a `gwt-queue` into one public lookup URL each, and can fetch them politely.

```bash
gwt-batch --queue cola.tsv --templates sources.tsv --limit 20                 # print: id, source, URL
gwt-batch --queue cola.tsv --templates sources.tsv --fetch --out raw/ --delay 10
```

The template file has one row per source: `source` (the queue's source key), `url` with `{query}` and `{label}` placeholders, `delay` in seconds, `note`. See `batch/sources.example.tsv`. Only searches that work with a plain GET can be templated; forms with tokens, logins and captchas cannot, and are not attempted.

Manners: `robots.txt` is read first and a disallowed URL is never fetched; one request at a time per host, at least `delay` seconds apart; a descriptive User-Agent; a host that answers 403 or 429, or shows a captcha or "too many requests" page, is left alone for the rest of the run. Pages are saved as `OUT/<task id>.html` for the reader. The queue is never changed: the reader decides if a task is done (`gwt-queue done`).

Exit 0 when a URL was built, 1 when no open task has a template, 2 on a bad template file.
