# gwt-dates

Dates in French acts from 1792 to 1805 are Republican ("14 germinal an XIII"), and older acts write September as "7bre". Both are easy to misread and to convert by hand, and a day or a month off then gets copied into the tree. `gwt-dates` converts them by calculation, offline.

Standard library only. Run it as `dates/gwt-dates` or `python3 dates/gwt_dates.py`, or install the package for `gwt-dates`.

```sh
gwt-dates r2g "14 germinal an XIII"              # 1805-04-04
gwt-dates r2g "14 germinal an 13" --as-note      # 14 germinal an 13 = 04/04/1805
gwt-dates r2g "3 jour complementaire an II"
gwt-dates g2r 1805-04-04                         # 14 germinal an XIII
gwt-dates parse "30 7bre 1812"                   # 1812-09-30
gwt-dates parse "3 de marzo de 1805"             # 1805-03-03
gwt-dates bef "14 germinal an XIII"              # bef 1805-04-04
gwt-dates age 1850-06-10 --years 63              # between 1786-06-11 and 1787-06-10
gwt-dates age 1850-06-10 --years 63 --months 2 --days 5   # 1787-04-05
```

- **Republican to Gregorian** reads the day, a month name (accents and case ignored) or "jour complémentaire" / "sansculottide", and the year in digits or Roman numerals (an I to an XIV).
- **The calendar as applied.** An I began on 22 September 1792. Years III, VII and XI had a sixth complementary day. The last day is 10 nivôse an XIV (31 December 1805). A date outside that span, a day 31, or a sixth complementary day in a common year is refused with exit 2.
- **`--as-note`** prints the "14 germinal an 13 = 04/04/1805" form that [`gwt-check republican`](check.md) reads back, so a note written with it passes that check. The test suite runs the notes of every regular month through `gwt-check republican` and finds no difference. **Limit:** `gwt-check republican` does not read the complementary days (`jour complémentaire`, `sansculottide`): a note for one is ignored, not checked. `gwt-dates` does convert them.
- **Old-style months**: `7bre` is September, `8bre` October, `9bre` November, `Xbre` and `10bre` December. Reading `9bre` as September moves the date two months. French and Spanish month names are read too.
- **A year starts on 22, 23 or 24 September.** An XIII began on 23 September 1804. Always convert; do not count from memory.

- **`bef`** reads a date in any of the forms above (or `YYYY-MM-DD`) and prints it as `bef YYYY-MM-DD`. Use it for an issue date ("acte délivré le ..."): the act was issued on that day, so the event is before it, and [`gwt-acts`](acts.md) rejects an issue date that is not marked so.
- **`age`** gives a birth from the date of the event (`YYYY-MM-DD`, Gregorian) and the age the act states. With years only it prints the range the age allows, both ends included: a man aged 63 on 10 June 1850 was born from 11 June 1786 to 10 June 1787. With `--months` and/or `--days` it prints one date, counting back years, then months, then days. It refuses a day that does not exist (29 February with a whole number of years back, 31 March minus a month) a negative age and an age that goes back before year 1 (exit 2, no date): decide those by hand. An age in an act is what the priest or the clerk wrote; it is often rounded, so treat the result as a lead, never as the birth date.

Exit 0 on success, 2 on a date it cannot read or that does not exist.
