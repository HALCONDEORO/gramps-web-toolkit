"""gwt-fetch: pull archive images and text without a browser."""
from __future__ import annotations

import argparse
import sys

from . import boe, coverage, crop, digibib, europeana, gazeta, geo, iiif, index_locator, leads, names, notarial, touraine, variants


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
    s.add_argument("url", nargs="?", help="manifest URL or viewer URL (omit with --image-base)")
    s.add_argument("--image-base", metavar="URL",
                   help="a IIIF Image API base or its info.json, no manifest needed; {page} in it takes each --pages number")
    s.add_argument("--native", action="store_true",
                   help="fetch each canvas at the width its info.json gives, refusing a rescaled file")
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

    s = sub.add_parser("crop", help="cut a saved image into a box, a grid or a half (needs Pillow)")
    s.add_argument("images", nargs="+", metavar="IMG")
    how = s.add_mutually_exclusive_group(required=True)
    how.add_argument("--box", help="x,y,w,h in pixels")
    how.add_argument("--grid", help="columns x rows, e.g. 2x3")
    how.add_argument("--half", choices=["left", "right"])
    s.add_argument("--from-size", metavar="WxH",
                   help="the --box was measured on an image of this size (a preview); scale it to the real image")
    s.add_argument("--overlap", default="0%", help="with --grid, how far each tile reaches into its neighbours, e.g. 10%%")
    s.add_argument("--max", type=int, help="shrink each piece so its longest side is at most this many pixels")
    s.add_argument("--out", help="write pieces and their manifest.tsv rows here (default: beside each image)")

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
    ts.add_argument("--start", type=int, default=None, help="list mode: result offset for the next page (default 0)")
    ts.add_argument("--limit", type=int, default=None, help="list mode: most rows printed (default 80)")
    ts.add_argument("--table", action="store_true",
                    help="table mode sorted by cote: ark, cote, date, place, kind, media count, title")
    ts.add_argument("--page", type=int, default=None, help="with --table: the results page, from 1 (default 1)")
    ts.add_argument("--per-page", type=int, default=None, choices=touraine.TABLE_SIZES, help="with --table: rows per page (default 20)")
    tc = t.add_parser("catalogue", help="catalogue a place by year and cote; completeness UNKNOWN, gaps withheld",
                      description="Print TSV by year and cote with row provenance. Completeness is UNKNOWN: no captured "
                      "positive terminal layout and site-owned marker contract. All gaps withheld; exit 1.")
    tc.add_argument("query", metavar="place", help="place search value, sent unchanged (including a leading '=')")
    tc.add_argument("--per-page", type=int, default=20, choices=touraine.TABLE_SIZES,
                    help="rows requested per page (default 20)")
    tc.add_argument("--max-pages", type=int, default=100,
                    help="request ceiling, 1 to 100 including the terminal-page candidate (default 100); reaching it is incomplete")
    tp = t.add_parser("pages", help="list the pages of one register, or download a few")
    tp.add_argument("ark", help="the ark name from `search`")
    tp.add_argument("--pages", help="1-based page numbers, e.g. 1-5,9,12-")
    tp.add_argument("--max", type=_max_images, default=touraine.MAX_IMAGES,
                    help=f"most images one run downloads, 1 to {touraine.MAX_IMAGES} (default {touraine.MAX_IMAGES})")
    tp.add_argument("--out", default=".")
    tp.add_argument("--list", action="store_true", help="print page numbers and uuids, download nothing")

    s = sub.add_parser(
        "variants", help="offline surname search suggestions",
        description="Offline search suggestions only; never identity or merge evidence. "
                    "Print unique sorted spellings, preserving the original (1-128 characters).")
    s.add_argument("--surname", required=True, help="literal surname, including apostrophes or regex punctuation")
    s.add_argument("--file", help="local JSON object mapping exact surnames to lists of spellings; no default file")
    s.add_argument("--rules", action="store_true",
                   help="single edits of the original only: s/z, c/s, ph/f, y/i, double ASCII consonants; no recursion")
    s.add_argument("--regex", action="store_true",
                   help="one escaped Python regex with case-insensitive (?i); fold input accents with "
                        "gwt_fetch.names.fold (NFKD, lowercase, remove combining marks) before matching")

    s = sub.add_parser("coverage", help="which views of a register a sweep read and which it missed; offline")
    s.add_argument("total", type=int, help="number of views in the register")
    s.add_argument("--read", action="append", help="views read, e.g. 1-5,9,12-; may be repeated")
    s.add_argument("--dir", help="a download folder: files named NNNN_label count as that view")
    s = sub.add_parser("notarial-plan", help="local notarial catalogue research candidates; offline",
                       description="Plan a candidate search chain from local JSON catalogue metadata. "
                                   "Years are inclusive; no contract or kinship is inferred. No network or file writes.")
    s.add_argument("catalogue", help="local JSON file (gwt-notarial-catalogue/1)")
    s.add_argument("--locality", required=True, help="whole locality name as supplied in the catalogue")
    s.add_argument("--from", dest="year_from", required=True, type=int, help="inclusive first year (1-9999)")
    s.add_argument("--to", dest="year_to", required=True, type=int, help="inclusive last year (1-9999)")
    s = sub.add_parser("index-locate", help="exact local index-to-image crosswalk; offline")
    s.add_argument("reference", help="local JSON: book, folio/face or page, source and checked date")
    s.add_argument("--mapping", help="optional exact local JSON mapping with explicit zero/one image base")
    leads.add_parser(sub)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "leads":
        try:
            return leads.run(args, None)
        except (OSError, ValueError) as exc:
            print(f"gwt-fetch: {exc}", file=sys.stderr)
            return 2
    if args.cmd == "variants":
        return variants.run(args)
    if args.cmd == "names" and not args.source:
        args.source = ["gazeta", "boe"]
    mod = {"iiif": iiif, "digibib": digibib, "gazeta": gazeta, "boe": boe, "names": names, "europeana": europeana, "geo": geo, "touraine": touraine, "coverage": coverage, "notarial-plan": notarial, "index-locate": index_locator, "crop": crop}[args.cmd]
    if args.cmd == "touraine" and args.action == "catalogue":
        from . import touraine_catalogue
        mod = touraine_catalogue
    try:
        if args.cmd in ("coverage", "notarial-plan", "index-locate"):
            return mod.run(args, None)
        from .net import Client
        client = Client(delay=args.delay)
        return mod.run(args, client)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # network and parse errors: one line, non-zero exit
        print(f"gwt-fetch: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
