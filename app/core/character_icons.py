"""Official icon names and explicit character-book joins; no name-based guesses."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .date_master import validate_date_master

def icon_name(character_id):
    if type(character_id) is not int or not 0 < character_id <= 999999:
        raise ValueError('Unsupported character icon ID')
    return f'icon_l_{character_id:06d}'

def icon_hash(character_id):
    return hashlib.md5(icon_name(character_id).encode('ascii')).hexdigest()

def icon_asset_path(character_id):
    return f'assets/ultra/images/character/dmm/i/{icon_hash(character_id)}.bin'

def is_icon_image(data):
    if len(data) < 24 or not data.startswith(b'\x89PNG\r\n\x1a\n') or data[12:16] != b'IHDR':
        return False
    width, height = (int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big'))
    return 0 < width == height <= 512

def build_icon_index(payload):
    validate_date_master(payload)
    if payload['schemaVersion'] != 2:
        raise ValueError('The version 2 character/date export is required')
    books = {row['bookId']: row for row in payload['masterCharacterBook']}
    result = {}
    for row in payload['masterDateCharacters']:
        base, book_id = (row.get('baseCharacterId'), row.get('bookId'))
        if row.get('characterId') != base or row.get('bookOrderNum') != 1 or book_id not in books:
            continue
        if type(base) is not int or not 0 < base <= 999999:
            continue
        if type(book_id) is not int or book_id <= 0:
            continue
        record = {'characterId': base, 'name': books[book_id].get('name'), 'bookNum': books[book_id].get('bookNum')}
        if str(book_id) in result and result[str(book_id)] != record:
            raise ValueError('Ambiguous character-book icon mapping')
        result[str(book_id)] = record
    return {'kind': 'fkg-character-icon-index', 'schemaVersion': 1, 'masterVersion': payload.get('masterVersion'), 'books': result}

def load_icon_index(path: Path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Character icon index is too large')
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if payload.get('kind') != 'fkg-character-icon-index' or payload.get('schemaVersion') != 1:
        raise ValueError('Unsupported character icon index')
    books = payload.get('books')
    if not isinstance(books, dict):
        raise ValueError('Invalid character icon book mapping')
    result = {}
    for book, record in books.items():
        if not book.isdecimal() or int(book) <= 0 or (not isinstance(record, dict)):
            raise ValueError('Invalid character icon book record')
        icon_name(record.get('characterId'))
        result[book] = record
    return result

def merge_icon_labels(books, labels):
    """Use newer explicit ID/book labels without rewriting either source file."""
    result = {key: dict(row) for key, row in books.items()}
    for key, label in labels.items():
        previous = result.get(key)
        if previous and (previous.get('characterId') != label['characterId'] or previous.get('bookNum') != label['bookNum']):
            continue
        result[key] = (previous or {}) | label
    return result
