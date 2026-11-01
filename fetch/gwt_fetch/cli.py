"""gwt-fetch: pull archive images and text without a browser."""
from __future__ import annotations

import argparse
import sys

from . import boe, coverage, digibib, europeana, gazeta, geo, iiif, names, touraine
from .net import Client


def _max_images(text: str) -> int:
    n = int(text)
    if not 1 <= n <= touraine.MAX_IMAGES:
        raise argparse.ArgumentTypeError(f"must be 1 to {touraine.MAX_IMAGES}")
    return n


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gwt-fetch", description=__doc__)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host (default 1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def pick(sp, labels=True):
        sp.add_argument("--pages", help="1-based canvas positions, e.g. 1-5,9,12-")
        if labels:
            sp.add_argument("--labels", help="regular expression matched against canvas labels")
        sp.add_argument("--out", default=".", help="output folder; manifest.tsv is written there")
        sp.add_argument("--list", action="store_true", help="print what would be fetched, download nothing")

    s = sub.add_parser("iiif", help="download canvases from a IIIF manifest or a known viewer URL")
    s.add_argument("url")
    pick(s)

    s = sub.add_parser("digibib", help="download pages from a DIGIBIB viewer (BVPH, Defensa, ...)")
    s.add_argument("url", help=".../catalogo_imagenes/grupo.do?path=N")
    pick(s, labels=False)

    s = sub.add_parser("gazeta", help="search or fetch the historical Gaceta de Madrid (BOE Gazeta)")
    g = s.add_subparsers(dest="action", required=True)
    gs = g.add_parser("search", help="search; prints TSV")
    gs.add_argument("--text", help="words in the full text")
    gs.add_argument("--title", help="words in the title")
    gs.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    gs.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    gs.add_argument("--gazette", help="GAZ.ID of one title (7 = Gaceta de Madrid)")
    gs.add_argument("--limit", type=int, default=50)
    gf = g.add_parser("fetch", help="download the PDF and text of one or more references")
    gf.add_argument("refs", nargs="+", metavar="BOE-A-YYYY-N")
    gf.add_argument("--out", default=".")

    s = sub.add_parser("boe", help="search or fetch the modern BOE (1960 onwards)")
    b = s.add_subparsers(dest="action", required=True)
    bs = b.add_parser("search", help="search; prints TSV")
    bs.add_argument("--text", help="words in the full text")
    bs.add_argument("--title", help="words in the title")
    bs.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    bs.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    bs.add_argument("--limit", type=int, default=50)
    bf = b.add_parser("fetch", help="download the PDF and text of one or more references")
    bf.add_argument("refs", nargs="+", metavar="BOE-A-YYYY-N")
    bf.add_argument("--out", default=".")

    s = sub.add_parser("names", help="look people up by name in the Gaceta and the BOE, with the words around each hit")
    s.add_argument("person", nargs="+", metavar="Given/Surname1 Surname2")
    s.add_argument("--spell", action="append", default=[], help="another spelling of the first surname; repeatable")
    s.add_argument("--source", action="append", choices=["gazeta", "boe"], help="default: both")
    s.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    s.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    s.add_argument("--limit", type=int, default=50, help="hits per query and source")
    s.add_argument("--out", default=".", help="where the PDFs and text are saved")

    s = sub.add_parser("europeana", help="search Europeana (needs EUROPEANA_KEY); prints TSV with manifests")
    s.add_argument("query")
    s.add_argument("--provider", default="any", help='aggregator, e.g. Hispana for Spain; default any (no filter)')
    s.add_argument("--data-provider", help='institution, e.g. "Galiciana. Arquivo Dixital de Galicia"')
    s.add_argument("--country", help="e.g. spain")
    s.add_argument("--year-from")
    s.add_argument("--year-to")
    s.add_argument("--iiif-only", action="store_true", help="only records whose provider serves IIIF")
    s.add_argument("--rows", type=int, default=20)

    s = sub.add_parser("geo", help="geocode Spanish places through CartoCiudad; prints TSV with INE codes")
    s.add_argument("places", nargs="+")
    s.add_argument("--types", default="Municipio,poblacion",
                   help="CartoCiudad types to keep (Municipio, poblacion, toponimo, callejero); empty for all")
    s.add_argument("--province")
    s.add_argument("--limit", type=int, default=3, help="rows per place")

    s = sub.add_parser("touraine", help="search and read civil registers on archives.touraine.fr (Indre-et-Loire)")
    t = s.add_subparsers(dest="action", required=True)
    ts = t.add_parser("search", help="search registers; prints TSV with ark, title, dates and cote")
    ts.add_argument("query", help='e.g. "Commune 1793-1802"')
    ts.add_argument("--start", type=int, default=0, help="result offset for the next page")
    ts.add_argument("--limit", type=int, default=80)
    tp = t.add_parser("pages", help="list the pages of one register, or download a few")
    tp.add_argument("ark", help="the ark name from `search`")
    tp.add_argument("--pages", help="1-based page numbers, e.g. 1-5,9,12-")
    tp.add_argument("--max", type=_max_images, default=touraine.MAX_IMAGES,
                    help=f"most images one run downloads, 1 to {touraine.MAX_IMAGES} (default {touraine.MAX_IMAGES})")
    tp.add_argument("--out", default=".")
    tp.add_argument("--list", action="store_true", help="print page numbers and uuids, download nothing")

    s = sub.add_parser("coverage", help="which views of a register a sweep read and which it missed; offline")
    s.add_argument("total", type=int, help="number of views in the register")
    s.add_argument("--read", action="append", help="views read, e.g. 1-5,9,12-; may be repeated")
    s.add_argument("--dir", help="a download folder: files named NNNN_label count as that view")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    client = Client(delay=args.delay)
    if args.cmd == "names" and not args.source:
        args.source = ["gazeta", "boe"]
    mod = {"iiif": iiif, "digibib": digibib, "gazeta": gazeta, "boe": boe, "names": names, "europeana": europeana, "geo": geo, "touraine": touraine, "coverage": coverage}[args.cmd]
    try:
        return mod.run(args, client)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # network and parse errors: one line, non-zero exit
        print(f"gwt-fetch: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
