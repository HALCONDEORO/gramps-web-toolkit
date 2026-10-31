# gwt-acts

A reader of a register hands over what an act says. Before any of it goes into the tree, `gwt-acts` checks the card and, with a tree dump, says whether the person is already there. It reads a TSV and a [`gwt-check` dump](check.md) and writes nothing, to a tree or anywhere else. It never fetches.

Run it as `acts/gwt-acts` or `python3 acts/gwt_acts.py`, or install the package for `gwt-acts`. It uses [gwt-cross](cross.md) to match names and [gwt-dates](dates.md) to convert Republican dates, both in this repository.

```sh
gwt-acts check cards.tsv                 # is every card complete and consistent?
gwt-acts check cards.tsv --dump dump.json   # and is the person already in the tree?
```

## The card

One row per act, with a header. Columns, in any order: `card`, `type`, `given`, `surname`, `date`, `date_iso`, `republican`, `father`, `mother`, `state`, `cote`, `ark`, `url`, `filiation`, `zoom`, `place`, `notes`.

A card is **rejected** when:

- `cote`, `ark`, `url`, `filiation` or `date_iso` is empty;
- `state` is not `verificado-zoom`, `dudoso` or `por-confirmar-acta`;
- `state` is `verificado-zoom` and `zoom` is empty (the crop the identity lines were read on), or a name carries a `(?)`;
- `republican` is set and `gwt-dates` does not give `date_iso` for it;
- the date or the notes say "acte délivré" and `date_iso` does not start with `bef `: an issue date is not the date of the event.

A card with `father` or `mother` and any state other than `verificado-zoom` is marked **read-only**: its parents are not to be written.

## Looking the person up

With `--dump`, each card that was not rejected gets one verdict from the tree:

- `nouveau`: nobody in the tree fits;
- `already as P000123`: the surname, given name, year and place all agree;
- `possible duplicate (?) P000123`: they agree in part. Several ids mean equal scores, never a pick.

The score is the one `gwt-cross` uses, so spelling variants count (a surname that matches only by sound gets a `spelling:` note naming the tree's spelling). When the person is found and the act's full day differs from the tree's for the same kind of event (baptism or birth, burial or death), the line says `CONFLICT P000123 Birth: the act says ..., the tree says ...`. A tree date that is vaguer but contains the act's day (a month, a year) is not a conflict. Nothing is replaced: the conflict is for a person to decide.

## Output

A TSV on standard output: `card`, `verdict`, `detail`. Cells have no tab or newline and a leading `=`, `+`, `-` or `@` is neutralised. Exit 0 when no card was rejected and no conflict was found, 1 otherwise, 2 when a file cannot be read.

This is a lead for the reader, not an identification: a match is two sets of words that look alike.
