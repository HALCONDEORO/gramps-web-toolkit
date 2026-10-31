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
```

- **Republican to Gregorian** reads the day, a month name (accents and case ignored) or "jour complémentaire" / "sansculottide", and the year in digits or Roman numerals (an I to an XIV).
- **The calendar as applied.** An I began on 22 September 1792. Years III, VII and XI had a sixth complementary day. The last day is 10 nivôse an XIV (31 December 1805). A date outside that span, a day 31, or a sixth complementary day in a common year is refused with exit 2.
- **`--as-note`** prints the "14 germinal an 13 = 04/04/1805" form that [`gwt-check republican`](check.md) reads back, so a note written with it passes that check.
- **Old-style months**: `7bre` is September, `8bre` October, `9bre` November, `Xbre` and `10bre` December. Reading `9bre` as September moves the date two months. French and Spanish month names are read too.
- **A year starts on 22, 23 or 24 September.** An XIII began on 23 September 1804. Always convert; do not count from memory.

Exit 0 on success, 2 on a date it cannot read or that does not exist.
