"""Offline exact index-to-image crosswalk. No viewer requests or inferred offsets."""
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit
import re


def _source(url,checked):
    if not isinstance(url,str) or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in url):
        raise ValueError('source URL contains whitespace or controls')
    p=urlsplit(url)
    _=p.port
    if p.scheme not in ('http','https') or not p.hostname or p.username is not None or p.password is not None:
        raise ValueError('source requires HTTP(S) host without credentials')
    if not isinstance(checked,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',checked):
        raise ValueError('checked date requires YYYY-MM-DD')
    date.fromisoformat(checked)


def _reference(book,kind,number,side):
    if not isinstance(book,str) or not book.strip():
        raise ValueError('explicit book_id required')
    if kind not in ('folio','page') or type(number) is not int or number<1:
        raise ValueError('positive page or folio number required')
    if (kind=='folio' and side not in ('recto','verso')) or (kind=='page' and side is not None):
        raise ValueError('folio requires recto/verso; page has no face')


@dataclass(frozen=True)
class IndexLocator:
    book_id:str
    reference_kind:str
    number:int
    side:str|None
    source_url:str
    checked_on:str
    def __post_init__(self):
        _reference(self.book_id,self.reference_kind,self.number,self.side)
        _source(self.source_url,self.checked_on)


@dataclass(frozen=True)
class ImageMapping:
    book_id:str
    reference_kind:str
    number:int
    side:str|None
    image_number:int
    index_base:str
    source_url:str
    checked_on:str
    def __post_init__(self):
        _reference(self.book_id,self.reference_kind,self.number,self.side)
        _source(self.source_url,self.checked_on)
        if self.index_base not in ('zero','one') or type(self.image_number) is not int:
            raise ValueError('image number requires integer and explicit zero/one base')
        if self.image_number<(0 if self.index_base=='zero' else 1):
            raise ValueError('image number outside declared base')


def locate(reference,mapping=None):
    """Return a manual target only for an explicitly supplied exact crosswalk.

    Book IDs must already be resolved by the caller. Equal numbers in different
    books, folios, pages or faces are not equivalent. A mapping is a sourced
    caller declaration, not proof that an image exists or has been examined.
    """
    if not isinstance(reference,IndexLocator) or (mapping is not None and not isinstance(mapping,ImageMapping)):
        raise ValueError('IndexLocator and optional ImageMapping required')
    def key(row):
        return row.book_id, row.reference_kind, row.number, row.side
    if mapping is not None and key(reference)!=key(mapping):
        raise ValueError('mapping must match book, reference kind, number and face exactly')
    return dict(state='manual_target' if mapping is not None else 'mapping_needed',
                reference=dict(book_id=reference.book_id,kind=reference.reference_kind,number=reference.number,side=reference.side),
                index_source=dict(url=reference.source_url,checked_on=reference.checked_on),
                image_target=None if mapping is None else dict(number=mapping.image_number,index_base=mapping.index_base),
                mapping_source=None if mapping is None else dict(url=mapping.source_url,checked_on=mapping.checked_on),
                act_examined=False,identity_verified=False,
                scope_note='Index reference is a clue. No offset, viewer URL, image access or reading is inferred; examine the original image before any identity decision.')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field: ' + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-JSON constant: ' + value)


def _load(path, model):
    import json
    from pathlib import Path
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique,
                          parse_constant=_reject_constant)
        if not isinstance(data, dict):
            raise ValueError('input must be a JSON object')
        return model(**data)
    except (TypeError, RecursionError) as exc:
        raise ValueError('invalid locator JSON schema') from exc


def run(args, _client=None):
    """Read local caller-supplied metadata; never use a network client."""
    import json
    reference = _load(args.reference, IndexLocator)
    mapping = _load(args.mapping, ImageMapping) if args.mapping is not None else None
    print(json.dumps(locate(reference, mapping), ensure_ascii=True))
    return 0
