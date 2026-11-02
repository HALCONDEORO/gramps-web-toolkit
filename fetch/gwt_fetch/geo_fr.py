"""Read-only current French commune candidates. Never historical proof or tree edits."""
from __future__ import annotations
import math
import re
import sys
import unicodedata

API = 'https://geo.api.gouv.fr/communes'
DOCS = 'https://geo.api.gouv.fr/decoupage-administratif/communes'
COLUMNS = ['query', 'status', 'name', 'insee_code', 'department', 'lat', 'lng', 'source']


def fold(s):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()).split())


def text(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError(label + ': expected nonempty text without controls')
    return value.strip()


def coordinates(row):
    point = row.get('centre')
    if not isinstance(point, dict) or point.get('type') != 'Point':
        raise ValueError('commune centre must be a Point')
    xy = point.get('coordinates')
    if not isinstance(xy, list) or len(xy) != 2:
        raise ValueError('invalid coordinate pair')
    lng, lat = xy
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in xy):
        raise ValueError('coordinates must be finite numbers')
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ValueError('coordinates outside WGS84 range')
    return f'{lat:.6f}', f'{lng:.6f}'


def candidate(row, query):
    if not isinstance(row, dict):
        raise ValueError('commune must be an object')
    name = text(row.get('nom'), 'name')
    code = text(row.get('code'), 'INSEE code')
    dep = text(row.get('codeDepartement'), 'department')
    if not re.fullmatch(r'[0-9AB]{5}', code) or not re.fullmatch(r'(?:\d{2,3}|2[AB])', dep):
        raise ValueError('invalid administrative code')
    lat, lng = coordinates(row)
    return {'query': query, 'name': name, 'insee_code': code, 'department': dep,
            'lat': lat, 'lng': lng, 'source': API + '/' + code}


def geocode(client, query, department=None, code=None, limit=10):
    query = text(query, 'query')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError('limit must be 1..30')
    if department is not None and not re.fullmatch(r'(?:\d{2,3}|2[AB])', department):
        raise ValueError('invalid department')
    if code is not None and not re.fullmatch(r'[0-9AB]{5}', code):
        raise ValueError('invalid INSEE code')
    params = {'fields': 'nom,code,codeDepartement,centre', 'format': 'json', 'geometry': 'centre'}
    if department:
        params['codeDepartement'] = department
    url = API + '/' + code if code else API
    if not code:
        params['nom'] = query
    data = client.json(url, params=params)
    data = [data] if code and isinstance(data, dict) else data
    if not isinstance(data, list):
        raise ValueError('expected commune list')
    rows = [candidate(r, query) for r in data]
    rows = [r for r in rows if (not department or r['department'] == department) and (not code or r['insee_code'] == code)]
    rows.sort(key=lambda r: (r['insee_code'], r['name']))
    exact = [r for r in rows if fold(r['name']) == fold(query)]
    status = 'matched' if len(exact) == 1 and len(rows) == 1 else 'ambiguous' if rows else 'unmatched'
    # Fuzzy suggestions are never silently selected. Keep the complete candidate set before truncation.
    for r in rows:
        r['status'] = status if len(rows) <= limit else 'truncated-review'
        r['exact_name'] = fold(r['name']) == fold(query)
        r['requires_human_review'] = True
        r['current_commune_only'] = True
        r['candidate_count'] = len(rows)
    return rows[:limit]


def run(args, client):
    print('\t'.join(COLUMNS))
    count = 0
    for query in args.places:
        rows = geocode(client, query, args.department, args.insee_code, args.limit)
        for row in rows:
            print('\t'.join(str(row[k]) for k in COLUMNS))
        if not rows:
            print('gwt-fetch: no current French commune candidate for ' + repr(query), file=sys.stderr)
        count += len(rows)
    return 0 if count else 1
