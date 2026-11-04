"""Offline structural checks for a deceased-only CSV. Never an upload approval."""
import collections
import csv
import datetime as dt
import json
import re
import unicodedata
from urllib.parse import urlsplit

FIELDS = ('estado id_gramps nombre_literal_acta apellido_literal_acta tipo_fecha_principal '
          'fecha_principal indicador_fecha tipo_fecha_secundaria fecha_secundaria '
          'indicador_fecha_secundaria lugar_documentado fuente referencia_exacta url_fuente '
          'cita_gramps nota_evidencia lecturas_coincidentes criterio_fallecido '
          'discrepancias_no_exportadas relaciones_exportadas control_apellido_literal fecha_corte').split()


def date_ok(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def source_url_ok(value):
    if any(c.isspace() or unicodedata.category(c) == 'Cc' for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        return (parsed.scheme == 'https' and bool(host) and
                parsed.username is None and parsed.password is None and
                not any(c in host for c in ('?', '#', '/', '\\')))
    except ValueError:
        return False


def cutoff_ok(value):
    if date_ok(value):
        return True
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return 'T' in value and parsed.tzinfo is not None
    except ValueError:
        return False


def validate_rows(rows, *, surname_required=False):
    """Report row numbers and fixed reason codes only, never echo private values.

    Text claiming two readings/deceased status is not authenticated evidence.
    Even a structurally valid row remains pending-review and cannot be published.
    """
    results = []
    ids, identities = collections.defaultdict(list), collections.defaultdict(list)
    for number, row in enumerate(rows, 2):
        errors = []
        if not isinstance(row, dict) or set(row) != set(FIELDS) or any(not isinstance(v, str) for v in row.values()):
            results.append({'row': number, 'errors': ['schema'], 'state': 'blocked'})
            continue
        if any(any(unicodedata.category(c) == 'Cc' for c in v) for v in row.values()):
            errors.append('control-character')
        if row['estado'] != 'ELEGIBLE_NUCLEO;VALIDAR_ESQUEMA_DESTINO':
            errors.append('status-not-deceased-candidate')
        for key in ['id_gramps', 'nombre_literal_acta', 'fuente', 'referencia_exacta', 'url_fuente',
                    'cita_gramps', 'nota_evidencia', 'discrepancias_no_exportadas']:
            if not row[key].strip():
                errors.append('missing-' + key)
        if not source_url_ok(row['url_fuente']):
            errors.append('source-url')
        surname = row['apellido_literal_acta']
        if not surname.strip():
            if surname_required:
                errors.append('destination-requires-surname')
            if row['control_apellido_literal'] != 'VACIO_NO_INFERIR_APELLIDO':
                errors.append('surname-control')
        elif row['control_apellido_literal'] != 'literal':
            errors.append('surname-control')
        if row['relaciones_exportadas'] != 'NINGUNA':
            errors.append('relationships-forbidden')
        if row['lecturas_coincidentes'] != '2+; concordancia de nombre y fecha documentada en nota':
            errors.append('two-readings-claim-missing')
        if row['criterio_fallecido'] not in ('Nacimiento literal >110 años', 'Sepultura documentada'):
            errors.append('deceased-basis-unsupported')
        for suffix, kinds in [('', ('Birth', 'Death')), ('_secundaria', ('Baptism', 'Burial'))]:
            kind = row['tipo_fecha_principal'] if not suffix else row['tipo_fecha_secundaria']
            value = row['fecha_principal'] if not suffix else row['fecha_secundaria']
            indicator = row['indicador_fecha' + suffix]
            if kind not in kinds or indicator not in ('exacta', 'antes de') or not date_ok(value):
                errors.append('date-contract' + suffix)
        if not cutoff_ok(row['fecha_corte']):
            errors.append('cutoff-date')
        ids[row['id_gramps'].strip()].append(len(results))
        # Exact literal identity only. No surname inference, fuzzy merge or relation inference.
        identity = tuple(row[k].strip() for k in ('nombre_literal_acta', 'apellido_literal_acta',
                                                'tipo_fecha_principal', 'fecha_principal', 'indicador_fecha'))
        identities[identity].append(len(results))
        results.append({'row': number, 'errors': errors, 'state': 'blocked' if errors else 'pending-review'})
    for groups, code in [(ids, 'duplicate-id'), (identities, 'duplicate-literal-identity')]:
        for indexes in groups.values():
            if len(indexes) > 1:
                for i in indexes:
                    results[i]['errors'].append(code)
                    results[i]['state'] = 'blocked'
    if not results:
        results.append({'row': 0, 'errors': ['empty-input'], 'state': 'blocked'})
    return {'format': 'gwt-public-preflight/1', 'publish_allowed': False,
            'pending_requirements': ['independent-source-readings', 'deceased-evidence',
                                     'destination-contract', 'destination-deduplication'], 'rows': results}


def run(path, surname_required=False):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(FIELDS):
            raise ValueError('schema')
        report = validate_rows(reader, surname_required=surname_required)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 2 if any(row['errors'] for row in report['rows']) else 0
