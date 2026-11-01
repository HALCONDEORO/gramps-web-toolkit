"""Read-only research candidates from user-supplied public catalogue metadata."""
from __future__ import annotations

import copy
import json
import math
import os
import stat

from .coverage import parse_views, ranges
from .geo import _fold
from .names import fold

FORMAT = 'gwt-notarial-catalogue/1'
MAX_YEAR = 9999
WARNING = ('Catalogue coverage only: not evidence of a contract, marriage or parentage. '
           'Gaps mean no exact coverage supplied, not proof that records do not exist. '
           'Unknown dates require catalogue verification before selecting years.')


def _year(value):
    if type(value) is not int or not 1 <= value <= MAX_YEAR:
        raise ValueError('years must be integers between 1 and 9999')
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonblank string')
    return value


def _years(start, end):
    # Shared inclusive range parser; both bounds already validated (no open dates).
    return parse_views(f'{start}-{end}', MAX_YEAR)


def _validate_row(row, seen):
    if not isinstance(row, dict):
        raise ValueError('catalogue entries must be objects')
    for field in ('id', 'locality', 'notary', 'office', 'archive_ref', 'cote'):
        _text(row.get(field), field)
    if row['id'] in seen:
        raise ValueError(f'duplicate catalogue id: {row["id"]}')
    seen.add(row['id'])
    if row.get('certainty') not in ('exact', 'uncertain'):
        raise ValueError('certainty must be exact or uncertain')
    for field in ('start', 'end'):
        if field not in row:
            raise ValueError(f'{field} is required; use null for unknown')
        if row[field] is not None:
            _year(row[field])
    lo, hi = row['start'], row['end']
    if lo is not None and hi is not None and lo > hi:
        raise ValueError('catalogue range runs backwards')
    if 'date_text' in row and not isinstance(row['date_text'], str):
        raise ValueError('date_text must be a string')
    return lo, hi


def _overlaps(active):
    overlaps = []
    for year in sorted(active):
        ids = sorted(active[year])
        if len(ids) < 2:
            continue
        if overlaps and overlaps[-1]['period'][1] == year - 1 and overlaps[-1]['entries'] == ids:
            overlaps[-1]['period'][1] = year
        else:
            overlaps.append({'period': [year, year], 'entries': ids})
    return overlaps


def _append_candidate(row, lo, hi, start, end, chain, unknown, active):
    exact = row['certainty'] == 'exact' and lo is not None and hi is not None
    search_period = None
    if exact:
        first, last = max(start, lo), min(end, hi)
        if first > last:
            return
        search_period = [first, last]
        for year in _years(first, last):
            active.setdefault(year, []).append(row['id'])
    else:
        unknown.append(row['id'])
    chain.append({'id': row['id'], 'notary': row['notary'], 'office': row['office'],
                  'archive_ref': row['archive_ref'], 'cote': row['cote'],
                  'metadata': copy.deepcopy(row), 'search_period': search_period,
                  'status': 'candidate',
                  'next_step': ('Consult the supplied cote for these years; record literal evidence.' if exact else
                                'Verify dates in the supplied catalogue reference before choosing years.')})



def plan(data, locality, start, end):
    """No I/O: preserve supplied rows, clip exact dates, and keep uncertainty explicit."""
    _text(locality, 'locality')
    _year(start)
    _year(end)
    if start > end:
        raise ValueError('period runs backwards')
    if not isinstance(data, dict) or data.get('format') != FORMAT or not isinstance(data.get('entries'), list):
        raise ValueError(f'expected {FORMAT} with an entries list')

    chain, unknown, active, seen = [], [], {}, set()
    for row in data['entries']:
        lo, hi = _validate_row(row, seen)
        # The Spanish place fold drops non-ASCII letters: retain a Unicode guard.
        if _fold(row['locality']) != _fold(locality) or fold(row['locality']).strip() != fold(locality).strip():
            continue
        _append_candidate(row, lo, hi, start, end, chain, unknown, active)

    # Every entry stays separate, even a same-name notary in a different office.
    chain.sort(key=lambda r: (r['search_period'] is None, r['search_period'] or [], r['office'], r['id']))
    overlaps = _overlaps(active)
    return {'locality': locality, 'period': [start, end], 'chain': chain,
            'catalogue_coverage': ranges(sorted(active)),
            'catalogue_gaps': ranges(sorted(_years(start, end) - active.keys())),
            'overlaps': overlaps, 'unknown_coverage': sorted(unknown), 'warning': WARNING}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in catalogue")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-JSON constant in catalogue: " + value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('catalogue numbers must be finite')
    return number


def run(args, _client=None):
    # Only the explicit local JSON file is read; references are inert metadata.
    path = os.path.realpath(os.path.abspath(os.path.expanduser(args.catalogue)))
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise ValueError('catalogue must be a regular local file')
    with open(path, encoding='utf-8') as stream:
        data = json.load(stream, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
                         parse_float=_finite_float)
    result = plan(data, args.locality, args.year_from, args.year_to)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
