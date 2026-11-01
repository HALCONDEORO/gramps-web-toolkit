# gwt-fetch

A research agent that opens a browser to save a register page is slow and leaves no trace of where the file came from. `gwt-fetch` downloads the file straight from the portal and writes one `manifest.tsv` row per file, so every image in your tree can be traced back to its source URL and checksum.

Python 3.11 or newer with `requests`. Run it as [`fetch/gwt-fetch`](https://github.com/HALCONDEORO/gramps-web-toolkit/blob/main/fetch/gwt-fetch), symlinked onto your `PATH` if you like, or install the package (`pipx install gramps-web-toolkit`, see [Getting started](getting-started.md)) for `gwt fetch` and `gwt-fetch`. The code is in [`fetch/gwt_fetch/`](https://github.com/HALCONDEORO/gramps-web-toolkit/tree/main/fetch/gwt_fetch). Tests: `cd fetch && python3 -m pytest`.

`iiif` works with any IIIF archive in any country. `digibib`, `gazeta`, `boe` and `geo` cover Spanish sources only. `europeana` searches every provider in any country; `--provider Hispana` narrows it to Spain.

```
gwt-fetch iiif URL [--pages 1-5,9] [--labels REGEX] [--out DIR] [--list]
gwt-fetch digibib URL [--pages ...] [--out DIR] [--list]
gwt-fetch touraine search QUERY [--start N] [--limit N]
gwt-fetch touraine catalogue "<place>" [--per-page 20|40|80] [--max-pages N]
gwt-fetch touraine pages ARK [--pages 1-5,9] [--max N] [--out DIR] [--list]
gwt-fetch gazeta search (--text WORDS | --title WORDS) [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--limit N]
gwt-fetch gazeta fetch BOE-A-YYYY-N [...] [--out DIR]
gwt-fetch boe search (--text WORDS | --title WORDS) [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--limit N]
gwt-fetch boe fetch BOE-A-YYYY-N [...] [--out DIR]
gwt-fetch names Given/Surname1\ Surname2 [...] [--spell OTHER] [--source gazeta|boe] [--from Y-M-D] [--to Y-M-D] [--out DIR]
gwt-fetch europeana QUERY [--provider any|NAME] [--data-provider NAME] [--country NAME] [--year-from Y] [--year-to Y] [--iiif-only] [--rows N]
gwt-fetch geo PLACE [...] [--types Municipio,poblacion] [--province NAME] [--limit N]
gwt-fetch --delay 2 ...   # seconds between requests to one host, default 1
```

## What every download does

- **One row per file** in `DIR/manifest.tsv`: `file`, `label` (the canvas label or document title), `image_url` (the exact URL that served the bytes), `manifest_url` (the manifest or document page), `sha256`, `fetched_at` (UTC).
- **It never overwrites.** Files are written as `*.part` and renamed when complete, so a finished file on disk is always whole.
- **Re-running is resuming.** The same command skips finished files. A finished file without a row (the run died between rename and append) gets its row on the next run.
- **Polite by default.** One request per second per host, retries on 429 and 5xx with backoff, honours `Retry-After`. The user agent is `Mozilla/5.0 (compatible; gwt-fetch/0.1; +https://github.com/GeiserX/gramps-web-toolkit)`; Antenati refuses agents that don't start with `Mozilla/5.0`.
- **Exit status.** 0 when everything selected was fetched, 1 when some item failed (each failure is printed with the URLs tried and the HTTP status), 2 on a network or parse error.

Keep downloads out of the repo. Point `--out` at your research folder or `/tmp`.

## `iiif`: any IIIF manifest, or a viewer URL it knows

Give it a IIIF Presentation manifest URL (v2 or v3), or a viewer URL from a portal below. It downloads each selected canvas through the Image API at `full/max` (the largest size the server allows), falling back to `full/full` on v2 servers and then to the static image named in the manifest.

`--pages` takes 1-based canvas positions (`1-5,9,12-`); `--labels` takes a regular expression matched against the canvas label (`--labels '^pag\. (1|2)$'`). `--list` prints position, label and URL and downloads nothing.

| Portal | Works | Viewer URL to manifest |
|---|---|---|
| Portale Antenati (Italy) | Yes, tested | The unit page `https://antenati.cultura.gov.it/ark:/12657/an_ua4076426/` holds the link `https://dam-antenati.cultura.gov.it/antenati/containers/5vj8KVP/manifest`; `gwt-fetch` reads it from the page. Image requests need `Referer: https://antenati.cultura.gov.it/`, which `gwt-fetch` sends. `full/full` returns 403 and `full/max` works. The server caps output at 10 megapixels (`maxArea` in `info.json`), so a 4929×3781 scan arrives at 3610×2769. |
| Memòria Digital de Catalunya (and any CONTENTdm site) | Yes, tested | `https://mdc.csuc.cat/digital/collection/{alias}/id/{id}` becomes `https://mdc.csuc.cat/iiif/2/{alias}:{id}/manifest.json`. Images arrive at native size. |
| Europeana | Yes, tested | `https://www.europeana.eu/{lang}/item/{collection}/{id}` becomes `https://iiif.europeana.eu/presentation/{collection}/{id}/manifest`. When the provider has no Image API, Europeana's manifest points at its own thumbnail proxy (`api.europeana.eu/thumbnail/v2/url.json?uri=...`); `gwt-fetch` takes the original file from `uri` instead. The original can be a PDF. |
| Any Mirador or Universal Viewer link | Yes | A `?manifest=` or `?iiif-content=` parameter is used as the manifest. |
| Biblioteca Virtual de Prensa Histórica (BVPH) | No IIIF; use `digibib` | DIGIBIB viewer, no IIIF endpoint. |
| Biblioteca Virtual de Defensa | No IIIF; use `digibib` | DIGIBIB viewer, no IIIF endpoint. |
| Galiciana, Biblioteca Dixital de Galicia | Needs a browser | `biblioteca.galiciana.gal` answers record pages, viewer pages and image URLs with a JavaScript challenge page, also via Europeana's original-file links. `gwt-fetch` stops with a clear message; it does not solve the challenge. |
| Galiciana, Arquivo Dixital de Galicia | Yes, through `digibib` or Europeana | `arquivo.galiciana.gal/arpadweb` has no challenge; its single-file objects come back as PDF. |
| Biblioteca Digital Hispánica (BNE) | Needs a browser | `bdh-rd.bne.es` returns a 403 block page to scripted clients for viewer pages and for `pdf.raw` image URLs, from a datacenter address and from a home connection alike. No IIIF endpoint was found. |

Antenati traps from real downloads (two Italian birth registers of the 1850s and 1860s):

- The image server is slow: 10 to 60 seconds per image, about 20 on average. Five pages take close to two minutes, so run big selections in the background and let the resume logic carry a run that gets cut off.
- A plain `curl` gets 403 from both `antenati.cultura.gov.it` (search and unit pages) and the image server. The user agent has to start with `Mozilla/5.0`, and image requests need the portal as `Referer`.
- The web viewer shows about 1600 px and `full/full` returns a Cloudflare 403, which led one researcher to settle for 1600 px. `full/max` returns the largest size allowed: 3530×2716 for one page, 2122×1684 for another, depending on the scan.
- A canvas label is only `pag. N`. To find an act, list the canvases (`--list`), fetch a few pages around the estimate into `/tmp`, read the «Num. d'ordine» on each, then fetch just the right page into your research folder.

## `touraine`: civil registers of Indre-et-Loire (AD37)

`archives.touraine.fr` serves its digitised civil registers (cotes `6NUM...`) without a browser. `search` lists registers and `pages` lists or downloads their images. It is read only and keeps to one request a second whatever `--delay` says.

```
gwt-fetch touraine search "Commune 1793-1802"      # ark, title, dates, cote (80 results a page, --start for the next)
gwt-fetch touraine search --table "Commune naissances" --page 1   # table mode sorted by cote: ark, cote, date, place, kind, medias, title
gwt-fetch touraine catalogue "Exampleville"        # year and cote, with source provenance; mocked fixtures only so far
gwt-fetch touraine pages ARK --list                # page number, page URL, page uuid
gwt-fetch touraine pages ARK --pages 40-44 --out /tmp/reg
```

- `search --table` asks for the site's table mode sorted by cote (`mode=table`, `sort=referencecode_asc`), 20, 40 or 80 rows a page (`--per-page`, `--page` from 1; the stderr line names the next page). The ark and the cote stand in the same row, so the registers of one place are listed in cote order without opening each record. The site's own `-` for an empty cell becomes an empty cell; any other cell starting with `=`, `+`, `-` or `@` gets a leading quote. `--start` and `--limit` belong to the list mode and are refused with `--table`, as are `--page` and `--per-page` without it, whatever their value, even the default one (exit 2, before any request). A result count above 10 000 is shown as such by the site, so narrow the query.
- `catalogue "<place>"` follows the same table search from page 1, with 20 rows requested per page by default. It sends the place as a query value unchanged, including a leading `=`. Output is TSV sorted by numeric year, cote and ARK: `year`, `cote`, `ark`, `date`, `place`, `kind`, `medias`, `title`, `conflict`, `query`, `record_url`, `source_url`, `source_page`, `source_row`. Each register has a row for each year of its inclusive range; unknown dates have one `year=unknown` row. Source page and row are 1-based; the source URL includes the query and pagination parameters. No records are deduplicated by cote: different ARKs with the same cote keep both records and flag both as conflicts. Different metadata for one ARK also flags a conflict. All TSV cells have whitespace flattened and leading spreadsheet formula characters quoted.
- Catalogue completeness is currently **UNKNOWN**: this repository has no captured positive terminal-page contract for Touraine. Certification would require the expected complete site layout/structure together with a site-owned terminal/no-results marker established by captured source fixtures. Neither an empty (or header-only) `results-table`, nor absence of a `next` relation or known challenge wording proves completion. No live-site marker has been guessed. The parser stops at an empty table, retains earlier rows and provenance, and gives the missing contract as its incomplete reason. **All catalogue gap claims are withheld**, with exit 1, `pagination=incomplete`, `completeness=UNKNOWN`, `coverage=incomplete`, and no `uncovered=` output.
- Every table row must be a parsed record, its recognised following context row, or a header containing only header cells. Unrecognised or partly parsed tables, next links on empty tables, repeated pages, transport errors and refusals stop collection with a reason and retain previous rows. A short populated page requests the following page. `--max-pages` is a request ceiling from 1 to 100 (default 100), including the terminal-page candidate; reaching it leaves pagination incomplete. `--per-page` accepts 20, 40 or 80. Blank places and invalid options exit 2 before any request. Requests use the shared host pacing, follow no redirects and retry no refusals.
- Coverage also requires a nonempty result set whose dates are all exact four-digit ASCII years or ascending inclusive ranges (`1700-1702`, also with an en dash), present cotes, no conflicts, and explicit row `Lieu` metadata establishing one place throughout. Place identity uses Unicode NFC, case folding and collapsed whitespace only; accents, punctuation and spelling differences remain significant. Missing or mixed places, list separators or conjunctions, repeated place labels, line breaks or multiple place links withhold gaps. Query text and titles never establish place scope. Unknown or approximate dates withhold gaps. These checks remain necessary even if a terminal-page contract is established later; they do not prove survival, digitisation, every record type's coverage, or anything outside the returned dated span.
- Validation is **offline parser proof, not actual archive compatibility**. Existing Touraine table snippets are invented and do not capture a legitimate complete terminal page. Earlier positive tests incorrectly certified synthetic empty tables; that oracle was unsafe because denial and challenge pages can contain the same table. Independent denial-title, non-English human-challenge, reCAPTCHA-widget and unmarked-empty-table cases now all withhold gaps while preserving rows/provenance. No terminal positive control was added without source evidence. Date-union and explicit-place guard unit tests use an assumed completeness input solely to exercise those guards; they do not establish parser completion. No archive was fetched.

## `coverage`: which views of a register a sweep read

Some portals show every view as a double page, and the views are consecutive. A sweep that opens only the odd views, or the first of each pair, reads half of the register and raises no error. `coverage` is offline: it reads numbers, never a page.

```
gwt-fetch coverage 240 --read 1-100,150,200-       # views read by hand
gwt-fetch coverage 240 --dir /tmp/reg              # files named NNNN_label.jpg count as that view
```

- It prints how many views were read, which ranges are missing, and a warning when every missing view is even (or odd) and every other one was read.
- Exit 0 when every view was read, 1 when some are missing, 2 for a bad number, a view outside `1-total`, a backwards range or no `--read`/`--dir`.
- `--dir` counts only non-empty files whose name starts with four or more digits and an underscore, leaving out the downloader's `.part` files. It does not check that a file is a complete image.

## `digibib`: DIGIBIB viewers without IIIF

DIGIBÍS's DIGIBIB runs BVPH, the Biblioteca Virtual de Defensa, Galiciana and many regional digital libraries. None of these installs serve IIIF. The viewer URL is `.../catalogo_imagenes/grupo.do?path=N`. Each page view (`&presentacion=pagina&posicion=K`) names one image as `imagen_id.do?idImagen=M`, and that URL with `&formato=jpg` returns the full JPEG. `gwt-fetch digibib` walks those page views. When the object is a single file, `grupo.do` serves the file itself (often a PDF); it is saved as `0001_document.pdf`.

Tested: Biblioteca Virtual de Defensa (`grupo.do?path=359360`, 17 pages, 3764×2443 JPEGs), BVPH (`grupo.do?path=1000178404`, 72 pages), Galiciana Arquivo Dixital (`grupo.do?path=264703`, one PDF).

`prensahistorica.mcu.es` and the other `*.mcu.es` sites return HTTP 429 to some egress addresses (seen from a corporate VPN exit) on every request, while the same request from a home connection works. If every request gets 429, change network rather than raising `--delay`.

## `gazeta`: the historical Gaceta de Madrid

The BOE open data API (`/datosabiertos/api/`) covers consolidated legislation and the modern BOE and BORME summaries only, with no endpoint for the «Gazeta» colección histórica (1661-1959). `gwt-fetch` therefore uses the public pages, whose URLs are stable:

- Search: `GET https://www.boe.es/buscar/gazeta.php` with the form's own fields as query parameters. Each field is a `campo[i]`/`dato[i]` pair, joined by `operador[i]` (`and`, `or`, `not`):

  | Field | `campo[i]` | `dato[i]` | gwt-fetch flag |
  |---|---|---|---|
  | Full text | `campo[4]=DOC` | words; wrap a phrase in double quotes | `--text` |
  | Title | `campo[1]=TITULOS` | words | `--title` |
  | Gazette title | `campo[6]=GAZ.ID` | 7 = Gaceta de Madrid, 4 = Gaceta de la República, 8 = BOE, and 12 more in the form | `--gazette` |
  | Publication date | `campo[8]=FPU` | `dato[8][0]` and `dato[8][1]`, `YYYY-MM-DD`, 1661-01-01 to 1959-12-31 | `--from`, `--to` |
  | Also | `campo[0]=ID_HIST`, `campo[2]=RNG.ID`, `campo[3]=DEM.ID`, `campo[7]=NBO` | rank, department, issue number (`n/aaaa`) | not exposed |

  Plus `page_hits` (only 50, 200, 500, 1000 or 2000), `sort_field[0]=FPU`, `sort_order[0]=asc` and `accion=Buscar`. Results are `<li class="resultado-busqueda">` blocks with the title, «núm. N, de DD/MM/AAAA, página(s) P», the department and the item PDF with its reference. The output is TSV: `ref`, `date`, `gazette`, `number`, `page`, `department`, `title`, `pdf_url`, `issue`, `issue_pdf`. The site returns at most 2000 hits per search; narrow the dates for more.
- Whole issue: for the Gaceta de Madrid, `issue` is `GMD-{year}-{number}` and `issue_pdf` is `https://www.boe.es/gazeta/dias/{YYYY}/{MM}/{DD}/pdfs/GMD-{year}-{number}.pdf`, with the number unpadded (`GMD-1870-40`, not `-040`).
- Document by reference: `https://www.boe.es/buscar/doc.php?id=BOE-A-1870-1111` (title, «Publicado en», a link to the PDF).
- PDF: `https://www.boe.es/datos/pdfs/BOE//{year}/{issue:03}/A{first:05}-{last:05}.pdf`, the scanned page or pages of the original issue.

Traps we hit with real searches:

- `--text Surname1 Surname2` matches the two words anywhere in the item, so a two-surname search returns several times more hits than it should, mostly noise. `--text '"Surname1 Surname2"'` is the phrase, and returns the same hits the BOE search form returns in a browser.
- References are not all `BOE-A-`: `BOE-I-1892-51060` and `BOE-B-1957-12194` came back from the same search. `gazeta fetch` takes any `BOE-<letter>-<year>-<n>`.
- An item's title can hide what matters. `BOE-A-1864-10018` is titled «Gobierno de la provincia de Zaragoza. Registro de la Propiedad de Yeste.», yet its two pages are a long list of farms in Moratalla and Nerpio with every owner's name. The full-text search finds such names; the title never would. Read the PDF before you call a hit a homonym.
- The search does not index the annexes «Anuncios, avisos y Administración de Justicia» of many years. For those, download the whole issue (`issue_pdf`) and search its text.

To rank the acts that name one person as `gwt-rank` mentions, with a verdict from the birth year and the parents, use [`gwt-read boe search`](read.md#boe-and-gaceta-de-madrid).

`gazeta fetch REF` saves `REF.pdf` and `REF_text.txt`. The text is the HTML text when the BOE has transcribed it. Otherwise it is the PDF's own OCR layer through `pdftotext` (poppler), when that is installed. That OCR is rough on 19th-century type but good enough to search.

```
$ gwt-fetch gazeta search --text Betanzos --from 1890-01-01 --to 1890-03-31 --limit 3
ref	date	gazette	number	page	department	title	pdf_url
BOE-A-1890-676	1890-02-02	Gaceta de Madrid	33	325	Ministerio de la Gobernación	Estación Central de Telégrafos.- Telegramas recibidos ...	https://www.boe.es/datos/pdfs/BOE//1890/033/A00325-00325.pdf
$ gwt-fetch gazeta fetch BOE-A-1890-676 --out research/raw/fuentes/gaceta
```

## `boe`: the modern BOE, 1960 onwards

`gazeta` stops at 1959. From 1960 the BOE has its own search form, `GET https://www.boe.es/buscar/boe.php`, with a different result layout. `boe search` drives it and prints one TSV row per hit: `ref`, `date`, `number`, `department`, `title`, `doc_url`. `--text` takes words, or a phrase in double quotes; `--from` and `--to` take `YYYY-MM-DD`; `--limit` is rounded up to 50, 200, 500, 1000 or 2000 hits per request.

- The form rejects a request that leaves out fields ("los valores de búsqueda enviados son incorrectos"). `boe search` sends all of them, empty ones included, and the origin boxes `dato[0][1..5]` and `dato[0][T]`. A URL built by hand with only the text field fails.
- The full-text field is `campo[3]=DOC`, the title is `campo[1]=TITULOS`, the date range is `campo[6]=FPU` with `dato[6][0]` and `dato[6][1]` in `YYYY-MM-DD`. A `DD/MM/YYYY` range returns nothing, so `boe search` refuses any other form (and a date that does not exist, a range that ends before it starts, a `--limit` outside 1-2000, a blank `--text` or `--title`, digits that are not ASCII) with exit 2 before it asks the site. When the form answers 200 with its error box ("Los valores de búsqueda enviados son incorrectos"), `boe search` exits 2 with a message; it never reports that as 0 results.
- The search ignores accents: `Garcia` and `García` return the same hits.
- Some items (section B notices) come back with an empty `department` and `title`. The reference and date are still right.
- `boe fetch REF` is `gazeta fetch REF`. A modern document page marks its PDF `puntoPDF2`, and `gazeta fetch` now reads both markings. Many items say «Texto no disponible» and carry only the PDF; the text then comes from `pdftotext`.

```
$ gwt-fetch boe search --text '"Surname1 Surname2, Name"' --from 1975-01-01
$ gwt-fetch boe fetch BOE-A-1978-478 --out research/raw/fuentes/boe
```

## `names`: one person, every spelling, both collections

`names` is the search a person-first researcher repeats by hand: one person, the spellings of the surname, the Gaceta and the modern BOE. Write each person as `Given/Surname1 Surname2`; add `--spell` for another spelling of the first surname (the search already ignores accents, so `Garcia` and `García` are one query).

For every spelling it searches the two orders the gazettes print names in, `"Given Sur1 Sur2"` and `"Sur1 Sur2, Given"`, in `gazeta` and `boe`, merges the hits by reference, downloads each item (PDF and text, as `gazeta fetch` does) and prints the words around the match. Columns: `person`, `ref`, `date`, `source`, `query`, `context`.

- A hit says that the words occur in the item. `context` is the text around the first place the whole given name stands next to the full surname, in either order (`Given Sur1 Sur2` or `Sur1 Sur2, Given`), matching whole words only: `Juanita` is not `Juan` and `Ejemplote` is not `Ejemplo`. `(given name not adjacent)` means the full surname appears but not next to that given name; `(surname not in the OCR text: open the PDF)` means the OCR missed it, so read the page. None of this proves kinship; the command says so on every run.
- A common name returns many items. Narrow with `--from` and `--to` rather than reading all of them.
- Search a person only when the tree already treats them as dead. The command does not check.
- Bad arguments (a person without `/`, a date that is not `YYYY-MM-DD` or does not exist, `--from` after `--to`, `--limit` outside 1-2000, an empty `--spell`) exit 2 before any request. A failed download exits 1. Every TSV cell is one line.

```
$ gwt-fetch names 'Given/Surname1 Surname2' --spell Surnameone --from 1940-01-01 --to 1946-12-31 --out research/raw/fuentes/boe
```

## `europeana`: Spanish records with a manifest

The Europeana Search API needs a free key: get one at https://pro.europeana.eu/pages/get-api and `export EUROPEANA_KEY=...`. Without a key, the command prints a message and exits 0, so pipelines carry on.

By default it searches every provider. `--provider NAME` filters to one aggregator, for example `--provider Hispana` for Spain (4.5 million records: BVPH, Galiciana, BNE, the regional digital libraries and more). Use `--data-provider` to narrow to one institution as Europeana names it, for example `"Galiciana: Digital Library of Galicia"` or `"Virtual Library of Historical Press"`. `--country spain` keeps records whose Europeana country is that one, whoever provided them. Output is TSV with a `manifest` column for each record, which `gwt-fetch iiif` can fetch.

`--iiif-only` adds `sv_dcterms_conformsTo:*iiif*`, keeping only records whose provider serves a real Image API. In October 2026 that returns zero Hispana records: no Spanish provider exposes IIIF through Europeana, while France, Austria and the UK expose millions. For Spanish records, use the Europeana manifest without `--iiif-only`; `iiif` swaps each thumbnail for the provider's original file. Where that provider blocks scripted clients (Galiciana's library, BNE), you still need the browser.

## `geo`: Spanish places to coordinates and INE code

CartoCiudad's geocoder (IGN/CNIG, `https://www.cartociudad.es/geocoder/api/geocoder/candidates` then `/find`) turns a place name into coordinates, and every result carries the INE municipality code (`muniCode`, five digits: province plus municipality). No key needed.

```
$ gwt-fetch geo Betanzos Ortigueira --province "A Coruña" --limit 1
query	type	name	municipality	ine_code	province	lat	lng	cartociudad_id
Betanzos	poblacion	Betanzos	Betanzos	15009	A Coruña	43.278535	-8.213380	1200023058
Ortigueira	poblacion	Ortigueira	Ortigueira	15061	A Coruña	43.684475	-7.850976	1200024424
```

`--types` picks CartoCiudad result types: `Municipio` (the municipality, centroid), `poblacion` (a settlement), `toponimo` (named places such as churches and parish cemeteries, useful for parishes) and `callejero` (streets). Historical names that no longer exist won't match; geocode the modern municipality and keep the historical name in your own record.

## Spanish open-data projects we looked at

From the awesome-spain list (https://github.com/GeiserX/awesome-spain):

- **CartoCiudad / pycartociudad / caRtociudad**: used. `geo` calls the same public API directly, so it needs no extra dependency.
- **INE municipality codes** (municipios-provincias-comunidades-INE-spain, ccaa-provincias-municipios): not needed, because CartoCiudad already returns the INE code with each match.
- **spanish-names-surnames** (INE name and surname frequencies): not a fetch source. It may help a name-normalising step later.
- **boe-mcp, MCP-BOE, rOpenSpain/BOE, Datania BOE**: they wrap the modern BOE open data API, which does not cover the Gazeta collection. `gazeta` is the part that does.
- **API-CNIG, mapSpain, es-atlas**: map display and boundaries, outside what fetch does.
