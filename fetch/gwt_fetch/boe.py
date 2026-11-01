"""BOE modern collection (1960 onwards): the public search form `/buscar/boe.php`.

`gazeta` covers the Gaceta de Madrid and the BOE up to 1959. From 1960 the same
documents live behind a different form, with a different result layout and a
`puntoPDF2` link on the document page. Fetching a reference works the same way
for both, so `boe fetch` is `gazeta fetch`.
"""
from __future__ import annotations

import datetime
import html
import re
import sys
from urllib.parse import urljoin

from . import gazeta
from .gazeta import BASE, HITS, _text

SEARCH = BASE + "/buscar/boe.php"
COLUMNS = ["ref", "date", "number", "department", "title", "doc_url"]
MAX_LIMIT = HITS[-1]
ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # ASCII digits only: \d also reads Arabic-Indic digits, which the form refuses
REFUSED = re.compile(r"valores\s+de\s+b[uú]squeda\s+enviados\s+son\s+incorrectos", re.I)


class SearchRefused(RuntimeError):
    """The search form answered 200 with its error box: no result, and not an empty result."""


def problem(args) -> str:
    """What is wrong with the arguments of a search, or an empty string. The form rejects a date that is not
    YYYY-MM-DD and quietly returns nothing, so a bad date is refused here instead of read as no result."""
    for flag, value in (("--text", args.text), ("--title", args.title)):
        if value is not None and not value.strip():
            return f"{flag} is empty"
    if not (args.text or args.title):
        return "boe search needs --text or --title"
    for flag, value in (("--from", args.date_from), ("--to", args.date_to)):
        if value:
            if not ISO.fullmatch(value):  # Python 3.11 also reads 19790101 and 1979-W01-1
                return f"{flag} {value!r} is not a date written YYYY-MM-DD"
            try:
                datetime.date(int(value[:4]), int(value[5:7]), int(value[8:]))
            except ValueError:
                return f"{flag} {value!r} is not a date that exists"
    if args.date_from and args.date_to and args.date_from > args.date_to:
        return "--from is after --to"
    if not 1 <= args.limit <= MAX_LIMIT:
        return f"--limit must be between 1 and {MAX_LIMIT}"
    return ""


def cell(value: str) -> str:
    """A TSV cell: no tab, line break or carriage return from the page can start a new column or row."""
    return " ".join(value.split())


def search_params(text=None, title=None, date_from=None, date_to=None, limit=50) -> list:
    # The form insists on the origin boxes (ORIS 1-5 and T) and on every field being sent, even empty.
    p = [("campo[0]", "ORIS")] + [(f"dato[0][{k}]", k) for k in "12345T"] + [("operador[0]", "and"),
         ("campo[1]", "TITULOS"), ("dato[1]", title or ""), ("operador[1]", "and"),
         ("campo[2]", "DEM"), ("dato[2]", ""), ("operador[2]", "and"),
         ("campo[3]", "DOC"), ("dato[3]", text or ""), ("operador[3]", "and"),
         ("campo[4]", "NBOS"), ("dato[4]", ""), ("operador[4]", "and"),
         ("campo[5]", "NOF"), ("dato[5]", ""), ("operador[5]", "and"),
         ("operador[6]", "and"), ("campo[6]", "FPU"),
         ("dato[6][0]", date_from or ""), ("dato[6][1]", date_to or ""),
         ("page_hits", str(next((h for h in HITS if h >= limit), HITS[-1]))),
         ("sort_field[0]", "FPU"), ("sort_order[0]", "asc"),
         ("sort_field[1]", "ORI"), ("sort_order[1]", "asc"),
         ("sort_field[2]", "REF"), ("sort_order[2]", "asc"),
         ("accion", "Buscar")]
    return p


def parse_results(page: str) -> list[dict]:
    rows = []
    for li in re.findall(r'<li class="resultado-busqueda">(.*?)</li>\s*</ul>\s*</li>', page, re.S):
        plain = re.search(r"<p>(.*?)</p>", li, re.S)  # the title is the one paragraph without a class
        dem = re.search(r'<p class="linea-dem">(.*?)</p>', li, re.S)
        pub = re.search(r'<p class="linea-pub">(.*?)</p>', li, re.S)
        ref = re.search(r'title="Ref\. (BOE-[A-Z]-\d{4}-\d+)"', li)
        href = re.search(r'href="([^"]*doc\.php\?id=[^"]+)"', li)
        num = re.search(r"BOE (\S+) de (\d\d)/(\d\d)/(\d{4})", _text(pub.group(1))) if pub else None
        rows.append({
            "ref": ref.group(1) if ref else "",
            "date": f"{num.group(4)}-{num.group(3)}-{num.group(2)}" if num else "",
            "number": num.group(1) if num else "",
            "department": _text(dem.group(1)) if dem else "",
            "title": _text(plain.group(1)) if plain else "",
            "doc_url": urljoin(SEARCH, href.group(1)) if href else "",
        })
    return rows


def search(client, **kw) -> list[dict]:
    page = client.get(SEARCH, params=search_params(**kw))
    page.raise_for_status()
    if REFUSED.search(html.unescape(html.unescape(page.text))):  # the page writes b&amp;uacute;squeda: twice escaped
        raise SearchRefused("boe.es refused the search values (its error box), so this is not a result of 0")
    return parse_results(page.text)[: kw.get("limit") or None]


def run(args, client) -> int:
    if args.action == "search":
        bad = problem(args)
        if bad:
            print(f"gwt-fetch: {bad}", file=sys.stderr)
            return 2
        try:
            rows = search(client, text=args.text, title=args.title, date_from=args.date_from,
                          date_to=args.date_to, limit=args.limit)
        except SearchRefused as e:
            print(f"gwt-fetch: {e}", file=sys.stderr)
            return 2
        print("\t".join(COLUMNS))
        for r in rows:
            print("\t".join(cell(r[c]) for c in COLUMNS))
        print(f"gwt-fetch: {len(rows)} result(s)", file=sys.stderr)
        return 0
    return gazeta.run(args, client)
