"""Offline tests: a fake client answers from fixtures, so nothing touches the network."""
import hashlib
import argparse
import csv
import io
import json
from pathlib import Path

import pytest

from gwt_fetch import boe, digibib, europeana, gazeta, geo, iiif
from gwt_fetch.store import Store, slug


class Resp:
    def __init__(self, body=b"", ctype="text/html", status=200, url=""):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.headers = {"Content-Type": ctype}
        self.status_code = status
        self.ok = status < 400
        self.url = url

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def iter_content(self, n):
        yield self.content

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


class FakeClient:
    def __init__(self, routes):
        self.routes = routes  # url (or url prefix) -> Resp or callable
        self.calls = []
        self.kw = []

    def get(self, url, referer=None, stream=False, params=None, **kw):
        self.calls.append((url, referer, params))
        self.kw.append(kw)
        for key, val in self.routes.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                r = val(url, params) if callable(val) else val
                r.url = r.url or url
                return r
        return Resp(b"not found", status=404, url=url)

    def json(self, url, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def text(self, url, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.text


def ns(**kw):
    base = dict(pages=None, labels=None, out=".", list=False)
    base.update(kw)
    return argparse.Namespace(**base)


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


# ---------------------------------------------------------------- store

def test_slug_folds_accents():
    assert slug("Pàgina 1 · Año") == "pagina-1-ano"
    assert slug("") == "x"


def test_store_never_overwrites_and_records_orphans(tmp_path):
    s = Store(tmp_path)
    p = s.write("0001_a", [b"one"], "image/jpeg", "a", "http://i/1", "http://m")
    assert p.name == "0001_a.jpg"
    s.write("0001_a", [b"two"], "image/jpeg", "a", "http://i/1", "http://m")
    assert p.read_bytes() == b"one"
    # a finished file with no row (run died before the append) gets its row on the next run
    (tmp_path / "0002_b.jpg").write_bytes(b"x")
    s2 = Store(tmp_path)
    assert s2.done("0002_b", "b", "http://i/2", "http://m").name == "0002_b.jpg"
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["0001_a.jpg", "0002_b.jpg"]
    assert not s2.done("0003_c", "c", "", "")


def test_store_ignores_part_files(tmp_path):
    (tmp_path / "0001_a.jpg.part").write_bytes(b"half")
    assert Store(tmp_path).find("0001_a") is None


# ---------------------------------------------------------------- iiif

V2 = {
    "@context": "http://iiif.io/api/presentation/2/context.json",
    "label": "Archivio > Comune > 1870",
    "sequences": [{"canvases": [
        {"label": "pag. 1", "images": [{"resource": {"@id": "https://img/iiif/2/A/full/full/0/default.jpg",
                                                     "service": {"@id": "https://img/iiif/2/A",
                                                                 "@context": "http://iiif.io/api/image/2/context.json"}}}]},
        {"label": "pag. 2", "images": [{"resource": {"@id": "https://img/iiif/2/B/full/full/0/default.jpg",
                                                     "service": {"@id": "https://img/iiif/2/B/"}}}]},
        {"label": "copertina", "images": [{"resource": {"@id": "https://img/static/C.jpg"}}]},
    ]}],
}
V3 = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "label": {"es": ["Ferrol"]},
    "items": [
        {"label": {"none": ["p. 1"]}, "items": [{"items": [{"body": {
            "id": "https://api.europeana.eu/thumbnail/v2/url.json?uri=https%3A%2F%2Forig%2Fimg%3Fid%3D7&type=TEXT",
            "type": "Image"}}]}]},
        {"label": {"es": ["p. 2"]}, "items": [{"items": [{"body": {
            "id": "https://x/full.jpg", "type": "Image",
            "service": [{"id": "https://x/iiif3/Z", "type": "ImageService3"}]}}]}]},
    ],
}


def test_canvases_v2_and_candidates():
    cs = iiif.canvases(V2)
    assert [c.label for c in cs] == ["pag. 1", "pag. 2", "copertina"]
    assert iiif.candidates(cs[1])[:2] == ["https://img/iiif/2/B/full/max/0/default.jpg",
                                          "https://img/iiif/2/B/full/full/0/default.jpg"]
    assert iiif.candidates(cs[2]) == ["https://img/static/C.jpg"]


def test_canvases_v3_unwraps_europeana_thumbnail_and_v3_uses_max_only():
    cs = iiif.canvases(V3)
    assert iiif.candidates(cs[0]) == ["https://orig/img?id=7"]
    assert iiif.candidates(cs[1]) == ["https://x/iiif3/Z/full/max/0/default.jpg", "https://x/full.jpg"]


def test_select_pages_and_labels():
    cs = iiif.canvases(V2)
    assert [c.index for c in iiif.select(cs, "1,3", None)] == [1, 3]
    assert [c.index for c in iiif.select(cs, "2-", None)] == [2, 3]
    assert [c.index for c in iiif.select(cs, None, r"^pag")] == [1, 2]


def test_resolve_known_viewers():
    c = FakeClient({"https://antenati.cultura.gov.it/ark:/12657/an_ua1/": Resp(
        '<a href="https://dam-antenati.cultura.gov.it/antenati/containers/abc/manifest">x</a>')})
    assert iiif.resolve("https://antenati.cultura.gov.it/ark:/12657/an_ua1/", c) == (
        "https://dam-antenati.cultura.gov.it/antenati/containers/abc/manifest", "https://antenati.cultura.gov.it/")
    assert iiif.resolve("https://mdc.csuc.cat/digital/collection/comercUPF/id/22725", c)[0] == \
        "https://mdc.csuc.cat/iiif/2/comercUPF:22725/manifest.json"
    assert iiif.resolve("https://www.europeana.eu/es/item/2022719/abc_ent0", c)[0] == \
        "https://iiif.europeana.eu/presentation/2022719/abc_ent0/manifest"
    assert iiif.resolve("https://viewer/x?manifest=https%3A%2F%2Fm%2F1.json", c)[0] == "https://m/1.json"
    assert iiif.resolve("https://m/2.json", c)[0] == "https://m/2.json"


def test_iiif_run_falls_back_writes_manifest_and_resumes(tmp_path):
    routes = {
        "https://m/manifest": Resp(json.dumps(V2), "application/json"),
        "https://img/iiif/2/A/full/max/0/default.jpg": Resp(b"A-max", "image/jpeg"),
        "https://img/iiif/2/B/full/max/0/default.jpg": Resp(b"denied", "text/html", 403),
        "https://img/iiif/2/B/full/full/0/default.jpg": Resp(b"B-full", "image/jpeg"),
        "https://img/static/C.jpg": Resp(b"blocked page", "text/html", 200),
    }
    c = FakeClient(routes)
    rc = iiif.run(ns(url="https://m/manifest", out=str(tmp_path)), c)
    assert rc == 1  # canvas 3 only answered with HTML
    got = rows(tmp_path / "manifest.tsv")
    assert [(r["file"], r["image_url"]) for r in got] == [
        ("0001_pag-1.jpg", "https://img/iiif/2/A/full/max/0/default.jpg"),
        ("0002_pag-2.jpg", "https://img/iiif/2/B/full/full/0/default.jpg")]
    assert got[0]["manifest_url"] == "https://m/manifest" and len(got[0]["sha256"]) == 64
    n = len(c.calls)
    iiif.run(ns(url="https://m/manifest", out=str(tmp_path), pages="1-2"), c)
    assert len(c.calls) == n + 1  # only the manifest again; finished files are skipped


# ---------------------------------------------------------------- digibib

def test_digibib_pages_and_challenge(tmp_path):
    base = "https://bv.example/es/catalogo_imagenes/grupo.do"
    def page(url, params):
        if "presentacion=pagina" in url:
            n = url.split("posicion=")[1].split("&")[0]
            return Resp(f"<img src=\"../catalogo_imagenes/imagen_id.do?idImagen=90{n}&formato=jpg\">")
        return Resp("<title>BV &gt; Diario 1900</title><a href='x?numPags=3'>")
    c = FakeClient({base + "*": page,
                    "https://bv.example/es/catalogo_imagenes/imagen_id.do*": Resp(b"jpg", "image/jpeg")})
    assert digibib.run(ns(url=base + "?path=5", out=str(tmp_path), pages="2-3"), c) == 0
    got = rows(tmp_path / "manifest.tsv")
    assert [r["file"] for r in got] == ["0002_p-2.jpg", "0003_p-3.jpg"]
    assert "idImagen=902&formato=jpg" in got[0]["image_url"] and got[0]["label"] == "BV > Diario 1900 p. 2"

    blocked = FakeClient({base + "*": Resp("<script>eval(function(p,a,c,k,e,d){...})</script>")})
    with pytest.raises(SystemExit, match="JavaScript challenge"):
        digibib.run(ns(url=base + "?path=5", out=str(tmp_path / "b")), blocked)


def test_digibib_single_file_object(tmp_path):
    url = "https://arq.example/gl/catalogo_imagenes/grupo.do?path=9"
    c = FakeClient({url: Resp(b"%PDF-1.7", "application/pdf")})
    assert digibib.run(ns(url=url, out=str(tmp_path)), c) == 0
    assert (tmp_path / "0001_document.pdf").read_bytes() == b"%PDF-1.7"


# ---------------------------------------------------------------- gazeta

RESULTS = """
<ul><li class="resultado-busqueda">
  <p class="linea-pub">Dirección general de Comunicaciones.- Subasta del correo entre Betanzos y Ferrol.</p>
  <div class="datosGaz"><dl class="nosize">
    <dt>Gaceta de Madrid:</dt><dd><abbr title="número">núm.</abbr> 40, de 09/02/1870, página 2</dd>
    <dt>Departamento:</dt><dd>Ministerio de la Gobernación</dd></dl></div>
  <ul><li class="puntoPDF2"><a href="/datos/pdfs/BOE//1870/040/A00002-00002.pdf" title="Documento PDF (Ref. BOE-A-1870-1111)">PDF</a></li>
  </ul>
</li>
<li class="resultado-busqueda">
  <p class="linea-pub">Junta Calificadora.- Relación de vacantes.</p>
  <div class="datosGaz"><dl class="nosize">
    <dt>Gaceta de Madrid:</dt><dd><abbr title="número">núm.</abbr> 43, de 12/02/1890, páginas 436 a 440</dd>
    <dt>Departamento:</dt><dd>Ministerio de Guerra</dd></dl></div>
  <ul><li class="puntoPDF2"><a href="/datos/pdfs/BOE//1890/043/A00436-00440.pdf" title="Documento PDF (Ref. BOE-A-1890-910)">PDF</a></li>
  </ul>
</li></ul>"""

DOCPAGE = """<h3 class="documento-tit">Dirección general de Comunicaciones.- Subasta.</h3>
<dl><dt>Publicado en:</dt>
<dd>«Gaceta de Madrid» <abbr title="número">núm.</abbr> 40, de 9 de febrero de 1870, páginas 2 a 2</dd></dl>
<li class="puntoPDF">
  <a title="Documento PDF" href="/datos/pdfs/BOE//1870/040/A00002-00002.pdf">PDF</a></li>
<div id="textoxslt">
  <p class="parrafo">Primera línea.</p><p class="parrafo">Segunda &aacute;.</p>
</div>
<!-- #textoxslt -->"""


def test_gazeta_parse_results():
    r = gazeta.parse_results(RESULTS)
    assert r[0] == {"ref": "BOE-A-1870-1111", "date": "1870-02-09", "gazette": "Gaceta de Madrid", "number": "40",
                    "page": "2", "department": "Ministerio de la Gobernación",
                    "title": "Dirección general de Comunicaciones.- Subasta del correo entre Betanzos y Ferrol.",
                    "pdf_url": "https://www.boe.es/datos/pdfs/BOE//1870/040/A00002-00002.pdf",
                    "issue": "GMD-1870-40",
                    "issue_pdf": "https://www.boe.es/gazeta/dias/1870/02/09/pdfs/GMD-1870-40.pdf"}
    assert r[1]["page"] == "436 a 440" and r[1]["ref"] == "BOE-A-1890-910"


def test_gazeta_search_params():
    p = dict(gazeta.search_params(text="Betanzos", date_from="1870-01-01", limit=120))
    assert p["dato[4]"] == "Betanzos" and p["dato[8][0]"] == "1870-01-01" and p["page_hits"] == "200"


def test_gazeta_fetch_writes_pdf_and_html_text(tmp_path):
    c = FakeClient({gazeta.DOC + "BOE-A-1870-1111": Resp(DOCPAGE),
                    "https://www.boe.es/datos/pdfs/BOE//1870/040/A00002-00002.pdf": Resp(b"%PDF", "application/pdf")})
    assert gazeta.fetch(c, "BOE-A-1870-1111", Store(tmp_path)) == 0
    assert (tmp_path / "BOE-A-1870-1111.pdf").read_bytes() == b"%PDF"
    assert (tmp_path / "BOE-A-1870-1111_text.txt").read_text() == "Primera línea.\n\nSegunda á."
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["BOE-A-1870-1111.pdf", "BOE-A-1870-1111_text.txt"]
    assert gazeta.fetch(c, "not-a-ref", Store(tmp_path)) == 1


# ---------------------------------------------------------------- boe (modern collection)

BOE_RESULTS = """
<ul><li class="resultado-busqueda">
  <p class="linea-dem">Ministerio de Ejemplo</p>
  <p class="linea-pub">BOE 8 de 10/01/1978
   - II. Autoridades y personal</p>
  <p>Orden de 29 de diciembre de 1977 por la que se aprueba un expediente de ejemplo.</p>
  <a href="../buscar/doc.php?id=BOE-A-1978-9001" class="resultado-busqueda-link-defecto" title="Ref. BOE-A-1978-9001"></a>
  <ul><li class="puntoHTML"><a href="../buscar/doc.php?id=BOE-A-1978-9001">Más...</a></li></ul>
</li></ul></li></ul>
<ul><li class="resultado-busqueda">
  <p class="linea-pub">BOE 246 de 13/10/1979
   - V-B. Anuncios</p>
  <a href="../buscar/doc.php?id=BOE-B-1979-9002" class="resultado-busqueda-link-defecto" title="Ref. BOE-B-1979-9002"></a>
  <ul><li class="puntoHTML"><a href="../buscar/doc.php?id=BOE-B-1979-9002">Más...</a></li></ul>
</li></ul>"""

MODERN_DOC = """<h3 class="documento-tit">Orden de ejemplo.</h3>
<dl><dt>Publicado en:</dt><dd>« BOE » núm. 8, de 10 de enero de 1978, páginas 461 a 470</dd></dl>
<ul class="enlaces-doc"><li class="puntoPDF2">
  <a title="Documento PDF de la publicación original" href="/boe/dias/1978/01/10/pdfs/A00461-00470.pdf">PDF</a></li></ul>
<div id="textoxslt"> Texto no disponible. Consulte el documento PDF de esta disposición. </div>
<!-- #textoxslt -->"""


def test_boe_parse_results_with_and_without_department_and_title():
    r = boe.parse_results(BOE_RESULTS)
    assert r[0] == {"ref": "BOE-A-1978-9001", "date": "1978-01-10", "number": "8",
                    "department": "Ministerio de Ejemplo",
                    "title": "Orden de 29 de diciembre de 1977 por la que se aprueba un expediente de ejemplo.",
                    "doc_url": "https://www.boe.es/buscar/doc.php?id=BOE-A-1978-9001"}
    assert r[1]["ref"] == "BOE-B-1979-9002" and r[1]["department"] == "" and r[1]["title"] == ""


def test_boe_search_params_send_every_field_and_iso_dates():
    p = dict(boe.search_params(text='"Apellido Uno, Nombre"', date_from="1979-10-01", date_to="1979-12-31", limit=120))
    assert p["dato[3]"] == '"Apellido Uno, Nombre"' and p["campo[3]"] == "DOC"
    assert p["dato[6][0]"] == "1979-10-01" and p["dato[6][1]"] == "1979-12-31" and p["page_hits"] == "200"
    assert [p[f"dato[0][{k}]"] for k in "12345T"] == list("12345T")
    assert {"campo[1]", "campo[2]", "campo[4]", "campo[5]"} <= set(p)


def _boe_args(**kw):
    base = dict(action="search", text="x", title=None, date_from=None, date_to=None, limit=50)
    return argparse.Namespace(**{**base, **kw})


@pytest.mark.parametrize("kw", [dict(text=None), dict(date_from="1979-13-01"), dict(date_to="01/02/1980"),
                                dict(date_from="19790101"), dict(date_from="1979-W01-1"), dict(date_from="1980-01-02", date_to="1980-01-01"),
                                dict(limit=0), dict(limit=2001), dict(date_from="١٩٧٩-١٠-٠١"),
                                dict(text="   "), dict(text="x", title=" "), dict(text=None, title="\t")])
def test_boe_search_refuses_bad_arguments_with_exit_2_and_asks_nothing(kw, capsys):
    assert boe.run(_boe_args(**kw), FakeClient({})) == 2
    assert "gwt-fetch:" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["19790101", "1979-W01-1", "1979-1-05", "01/02/1980", "1979-01-05T10:00"])
def test_boe_search_names_the_format_when_a_date_is_not_written_yyyy_mm_dd(value):
    assert "written YYYY-MM-DD" in boe.problem(_boe_args(date_from=value))


def test_boe_search_says_when_a_date_is_well_written_but_does_not_exist():
    assert "that exists" in boe.problem(_boe_args(date_from="1979-02-30"))


def test_boe_search_accepts_a_one_day_range_and_the_largest_limit():
    assert boe.problem(_boe_args(date_from="1980-01-01", date_to="1980-01-01", limit=2000)) == ""


def test_boe_search_keeps_a_title_with_a_line_break_in_one_cell(capsys):
    row = {"ref": "BOE-A-1978-9001", "date": "1978-01-10", "number": "8", "department": "D",
           "title": "uno\r\ndos\ttres", "doc_url": "u"}
    assert boe.cell(row["title"]) == "uno dos tres"


def test_gazeta_fetch_reads_the_pdf_link_of_a_modern_document(tmp_path):
    c = FakeClient({gazeta.DOC + "BOE-A-1978-9001": Resp(MODERN_DOC),
                    "https://www.boe.es/boe/dias/1978/01/10/pdfs/A00461-00470.pdf": Resp(b"%PDF", "application/pdf")})
    assert boe.run(argparse.Namespace(action="fetch", refs=["BOE-A-1978-9001"], out=str(tmp_path)), c) == 0
    assert (tmp_path / "BOE-A-1978-9001.pdf").read_bytes() == b"%PDF"


# ---------------------------------------------------------------- europeana

def test_europeana_skips_without_key(monkeypatch, capsys):
    monkeypatch.delenv("EUROPEANA_KEY", raising=False)
    assert europeana.run(argparse.Namespace(query="x"), FakeClient({})) == 0
    assert "EUROPEANA_KEY is not set" in capsys.readouterr().err


def test_europeana_params_and_row():
    p = europeana.params("k", "Ferrol", "Hispana", None, None, "1800", None, True, 20)
    assert ("qf", 'PROVIDER:"Hispana"') in p and ("qf", "YEAR:[1800 TO *]") in p
    assert ("qf", "sv_dcterms_conformsTo:*iiif*") in p
    r = europeana.row({"id": "/2022706/abc", "title": ["Le Ferrol"], "dataProvider": ["Galiciana"], "year": ["1880"]})
    assert r["manifest"] == "https://iiif.europeana.eu/presentation/2022706/abc/manifest"
    assert r["title"] == "Le Ferrol"


def test_europeana_default_provider_is_any():
    from gwt_fetch import cli
    args = cli.parser().parse_args(["europeana", "Ferrol"])
    assert args.provider == "any"
    p = europeana.params("k", "Ferrol", None, None, None, None, None, False, 20)
    assert not any(v.startswith("PROVIDER:") for k, v in p if k == "qf")


def test_europeana_run_prints_tsv(monkeypatch, capsys):
    monkeypatch.setenv("EUROPEANA_KEY", "k")
    body = {"success": True, "totalResults": 1, "items": [{"id": "/1/a", "title": ["T"]}]}
    c = FakeClient({europeana.API: Resp(json.dumps(body), "application/json")})
    args = argparse.Namespace(query="q", provider="Hispana", data_provider=None, country=None,
                              year_from=None, year_to=None, iiif_only=False, rows=5)
    assert europeana.run(args, c) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("id\ttitle") and out[1].startswith("/1/a\tT")


# ---------------------------------------------------------------- geo

def test_geo_filters_types_and_province():
    cands = [
        {"id": "1", "type": "callejero", "address": "RUA X", "muni": "Betanzos", "muniCode": "15009", "province": "A Coruña"},
        {"id": "15009", "type": "Municipio", "address": "Betanzos, A Coruña", "muni": "Betanzos",
         "muniCode": "15009", "province": "A Coruña"},
        {"id": "99", "type": "Municipio", "address": "Betanzos, Lugo", "muni": "Betanzos", "muniCode": "27000",
         "province": "Lugo"},
    ]
    c = FakeClient({geo.API + "/candidates": Resp(json.dumps(cands), "application/json"),
                    geo.API + "/find": Resp(json.dumps({"lat": 43.2752331, "lng": -8.2231386}), "application/json")})
    out = geo.geocode(c, "Betanzos", ["Municipio"], province="a coruna")
    assert out == [{"query": "Betanzos", "type": "Municipio", "name": "Betanzos, A Coruña", "municipality": "Betanzos",
                    "ine_code": "15009", "province": "A Coruña", "lat": "43.275233", "lng": "-8.223139",
                    "cartociudad_id": "15009"}]


# ---- touraine (Archives d'Indre-et-Loire): invented arks and pages, no network ----
from gwt_fetch import touraine  # noqa: E402

UUID_A = "11111111-2222-3333-4444-%012d"
TOUR_RESULTS = """
<ul><li class="element-list"><a href="https://archives.touraine.fr/ark:/37621/abc123def">x</a>
<h2> <span>Commune &amp; Test, naissances 1793-1802</span></h2>
<h3>Date</h3> <p><span>1793-1802</span></p><p class="referenceCodes">6NUM8/999/001</p></li>
<li class="element-list"><h2><span>no link here</span></h2></li>
<li class="element-list"><a href="/ark:/37621/zzz999yyy">y</a><h2><span>=cmd|calc</span></h2></li></ul>
"""


def visualizer(total, error="Invalid request: group index or ArkName or start/end boundaries values are not valid"):
    def answer(url, params):
        s, e = int(params["start"]), int(params["end"])
        if e > total or s > total:
            return Resp(json.dumps(error), "application/json", status=400)
        return Resp(json.dumps({str(n): {"url": f"{touraine.BASE}/ark:/37621/abc123def/{UUID_A % n}"}
                                for n in range(s, e + 1)}), "application/json")
    return answer


def test_touraine_parse_results_keeps_links_and_neutralises_formulas():
    rows = touraine.parse_results(TOUR_RESULTS)
    assert [r["ark"] for r in rows] == ["abc123def", "zzz999yyy"]
    assert rows[0] == {"ark": "abc123def", "title": "Commune & Test, naissances 1793-1802",
                       "date": "1793-1802", "cote": "6NUM8/999/001"}
    assert rows[1]["title"] == "'=cmd|calc"


@pytest.mark.parametrize("total", [0, 1, 99, 100, 101, 250, 300])
def test_touraine_list_pages_finds_the_exact_end(total):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(total)})
    pages = touraine.list_pages(c, "abc123def")
    assert sorted(pages) == list(range(1, total + 1))


def test_touraine_edepot_is_reported_not_guessed():
    c = FakeClient({touraine.BASE + "/visualizer/api": Resp('"Group with index 0 not found"', status=400)})
    with pytest.raises(touraine.Edepot):
        touraine.list_pages(c, "abc123def")


def test_touraine_parse_range():
    assert touraine.parse_range("1-3,9,12-", 14) == [1, 2, 3, 9, 12, 13, 14]
    assert touraine.parse_range(None, 3) == [1, 2, 3]
    assert touraine.parse_range("5-99", 7) == [5, 6, 7]
    for bad in ("x", "-", "1,,2"):
        with pytest.raises(ValueError):
            touraine.parse_range(bad, 10)


def _targs(tmp_path, **kw):
    base = dict(action="pages", ark="abc123def", pages=None, max=30, out=str(tmp_path), list=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_touraine_pages_list_prints_numbers_and_uuids(tmp_path, capsys):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(5)})
    assert touraine.run(_targs(tmp_path, list=True, pages="2-3"), c) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [l.split("\t")[0] for l in lines] == ["2", "3"] and lines[0].endswith(UUID_A % 2)
    assert not list(tmp_path.iterdir())


def test_touraine_refuses_to_download_a_whole_register(tmp_path, capsys):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(200)})
    assert touraine.run(_targs(tmp_path), c) == 2
    assert "more than --max 30" in capsys.readouterr().err
    assert not [u for u, *_ in c.calls if "/images/" in u]


def test_touraine_downloads_selected_pages_with_manifest_and_resumes(tmp_path):
    routes = {touraine.BASE + "/visualizer/api": visualizer(5),
              touraine.BASE + "/images/*": Resp(b"\xff\xd8jpeg", "image/jpeg")}
    c = FakeClient(routes)
    assert touraine.run(_targs(tmp_path, pages="1-2"), c) == 0
    rows = list(csv.DictReader(open(tmp_path / "manifest.tsv", encoding="utf-8"), delimiter="\t"))
    assert [r["file"] for r in rows] == ["abc123def_p0001.jpg", "abc123def_p0002.jpg"]
    assert rows[0]["image_url"] == f"{touraine.BASE}/images/{UUID_A % 1}.jpg"
    n = len([u for u, *_ in c.calls if "/images/" in u])
    assert touraine.run(_targs(tmp_path, pages="1-3"), c) == 0
    assert len([u for u, *_ in c.calls if "/images/" in u]) == n + 1


def _images(c):
    return [u for u, *_ in c.calls if "/images/" in u]


@pytest.mark.parametrize("answer", [Resp("<html>challenge</html>", "text/html"), Resp("", "text/html", status=403),
                                    Resp("", "text/html", status=429), Resp("", "text/html", status=302),
                                    Resp("x", "image/jpeg", status=500)])
def test_touraine_any_refusal_on_an_image_stops_the_whole_run(tmp_path, answer):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(5), touraine.BASE + "/images/*": answer})
    assert touraine.run(_targs(tmp_path), c) == 1
    assert len(_images(c)) == 1  # the first page was refused: no second page is asked for
    assert not list(tmp_path.glob("*.jpg"))


def test_touraine_a_refusal_on_page_3_keeps_the_first_two_and_asks_for_no_page_4(tmp_path):
    def image(url, params):
        return Resp(b"jpeg", "image/jpeg") if UUID_A % 3 not in url else Resp("", "text/html", status=403)
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(6), touraine.BASE + "/images/*": image})
    assert touraine.run(_targs(tmp_path), c) == 1
    assert sorted(p.name for p in tmp_path.glob("*.jpg")) == ["abc123def_p0001.jpg", "abc123def_p0002.jpg"]
    assert len(_images(c)) == 3


def test_touraine_requests_follow_no_redirect_and_are_never_retried(tmp_path):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(1), touraine.BASE + "/images/*": Resp(b"j", "image/jpeg")})
    touraine.run(_targs(tmp_path), c)
    assert c.kw and all(k.get("allow_redirects") is False for k in c.kw)
    assert c.retries == 0


@pytest.mark.parametrize("answer", [Resp('"Access denied"', "application/json", status=403),
                                    Resp('"Internal error"', "application/json", status=500),
                                    Resp('"Access denied"', "application/json", status=200),
                                    Resp("<html>challenge</html>", "text/html"),
                                    Resp('"Invalid request: something else"', "application/json", status=400),
                                    Resp("{}", "application/json", status=302)])
def test_touraine_only_the_exact_end_message_with_status_400_is_the_end(tmp_path, answer, capsys):
    c = FakeClient({touraine.BASE + "/visualizer/api": answer})
    assert touraine.run(_targs(tmp_path, list=True), c) == 1
    assert "stopped" in capsys.readouterr().err


def test_touraine_a_refused_search_stops(capsys):
    c = FakeClient({touraine.BASE + "/search/results": Resp("<html>anubis</html>", "text/html", status=403)})
    assert touraine.run(argparse.Namespace(action="search", query="x", start=0, limit=5), c) == 1


def test_touraine_max_cannot_be_raised_above_30():
    from gwt_fetch.cli import parser
    assert parser().parse_args(["touraine", "pages", "abc123def", "--max", "30"]).max == 30
    for bad in ("31", "0", "-1", "9999"):
        with pytest.raises(SystemExit):
            parser().parse_args(["touraine", "pages", "abc123def", "--max", bad])


def test_touraine_rejects_an_ark_that_could_change_the_url(tmp_path, capsys):
    c = FakeClient({})
    assert touraine.run(_targs(tmp_path, ark="../x?y=1"), c) == 2
    assert not c.calls


def test_touraine_never_goes_faster_than_one_request_a_second(tmp_path):
    class Slow(FakeClient):
        delay = 0.0
    c = Slow({touraine.BASE + "/visualizer/api": visualizer(1)})
    touraine.run(_targs(tmp_path, list=True), c)
    assert c.delay == 1.0


def test_touraine_search_prints_tsv_with_a_header(capsys):
    c = FakeClient({touraine.BASE + "/search/results": Resp(TOUR_RESULTS)})
    assert touraine.run(argparse.Namespace(action="search", query="Commune 1793", start=0, limit=1), c) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "ark\ttitle\tdate\tcote" and len(out) == 2
    assert c.calls[0][2]["scope"] == "archival"


def test_touraine_a_server_error_is_an_error_not_the_end_of_the_register():
    c = FakeClient({touraine.BASE + "/visualizer/api": Resp("<html>oops</html>", status=500)})
    with pytest.raises(touraine.Blocked):
        touraine.list_pages(c, "abc123def")


def test_touraine_run_itself_caps_a_huge_max_at_30(tmp_path, capsys):
    c = FakeClient({touraine.BASE + "/visualizer/api": visualizer(100)})
    assert touraine.run(_targs(tmp_path, max=500), c) == 2
    assert not _images(c)


@pytest.mark.parametrize("status", [403, 500, 200])
def test_touraine_the_end_message_counts_only_with_status_400(tmp_path, status, capsys):
    end = Resp(json.dumps(touraine.END_OF_REGISTER), "application/json", status=status)
    c = FakeClient({touraine.BASE + "/visualizer/api": end})
    assert touraine.run(_targs(tmp_path, list=True), c) == 1
    assert "stopped" in capsys.readouterr().err


ERROR_BOX = "<html><div class='mensabox'>Los valores de b\u00fasqueda enviados son incorrectos</div></html>"


def test_boe_search_error_box_is_an_error_and_not_zero_results(capsys):
    c = FakeClient({boe.SEARCH: Resp(ERROR_BOX)})
    assert boe.run(_boe_args(), c) == 2
    cap = capsys.readouterr()
    assert "refused" in cap.err and "result(s)" not in cap.err and cap.out == ""


def test_boe_search_page_without_results_and_without_the_box_is_still_zero(capsys):
    assert boe.run(_boe_args(), FakeClient({boe.SEARCH: Resp("<html>nada</html>")})) == 0
    assert "0 result(s)" in capsys.readouterr().err



ERROR_BOX_REAL = ('<div class="mensajeError"><p>Los valores de b&amp;uacute;squeda enviados son incorrectos.</p></div>'
                  '<div>Los valores de b&uacute;squeda enviados son incorrectos</div>')


@pytest.mark.parametrize("page", [ERROR_BOX_REAL, ERROR_BOX_REAL.split("</div>")[0], "<p>Los valores de b&uacute;squeda enviados son incorrectos</p>"])
def test_boe_search_error_box_as_the_site_writes_it_with_html_entities(page, capsys):
    assert boe.run(_boe_args(), FakeClient({boe.SEARCH: Resp(page)})) == 2
    assert "result(s)" not in capsys.readouterr().err


REAL_ERROR_PAGE = Path(__file__).parent / "data" / "boe_error_unicode_date.html"


def test_boe_search_real_error_page_is_exit_2_and_not_zero_results(capsys):
    raw = REAL_ERROR_PAGE.read_bytes()  # captured from boe.es for a date written with Arabic-Indic digits
    assert hashlib.sha256(raw).hexdigest() == "d677a09471c26621d2ef6f9f0c2570d6390a3412541ad741a8d7fb36a92a60a7"
    assert boe.run(_boe_args(), FakeClient({boe.SEARCH: Resp(raw)})) == 2
    assert "result(s)" not in capsys.readouterr().err
