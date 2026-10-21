"""Display-only costume labels from explicitly exported character master fields."""
from __future__ import annotations
import json
from pathlib import Path

def _text(value, allow_empty=False):
    return isinstance(value, str) and len(value) <= 160 and (allow_empty or bool(value.strip())) and (not any((ord(char) < 32 for char in value)))

def build_character_labels(payload):
    if not isinstance(payload, dict) or payload.get('kind') != 'fkg-character-labels' or payload.get('schemaVersion') != 1:
        raise ValueError('Not a supported character-label export')
    rows = payload.get('characters')
    if not isinstance(rows, list) or not 0 < len(rows) <= 10000:
        raise ValueError('Missing or invalid character labels')
    books = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid character label row')
        book, identity, number = (row.get(key) for key in ('bookId', 'characterId', 'bookNum'))
        if type(book) is not int or book <= 0 or type(identity) is not int or (not 0 < identity <= 999999) or (type(number) is not int) or (number <= 0):
            raise ValueError('Invalid character label identity')
        name, dress, book_name = (row.get(key) for key in ('nickname', 'dressName', 'bookName'))
        if not _text(name) or not _text(dress, True) or (not _text(book_name)):
            raise ValueError('Invalid character label text')
        label = f'{name.strip()}({dress.strip()})' if dress.strip() else book_name.strip()
        if not _text(label):
            raise ValueError('Character display name is too long')
        record = {'characterId': identity, 'bookNum': number, 'name': label}
        if str(book) in books and books[str(book)] != record:
            raise ValueError('Conflicting labels for one character book')
        books[str(book)] = record
    return {'kind': 'fkg-character-label-index', 'schemaVersion': 1, 'books': books}

def load_character_labels(path: Path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Character label index is too large')
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict) or payload.get('kind') != 'fkg-character-label-index' or payload.get('schemaVersion') != 1 or (not isinstance(payload.get('books'), dict)):
        raise ValueError('Invalid character label index')
    for book, row in payload['books'].items():
        if not book.isdecimal() or int(book) <= 0 or (not isinstance(row, dict)) or (type(row.get('characterId')) is not int) or (not 0 < row['characterId'] <= 999999) or (type(row.get('bookNum')) is not int) or (row['bookNum'] <= 0) or (not _text(row.get('name'))):
            raise ValueError('Invalid character label mapping')
    return payload['books']

def apply_character_label(card, labels):
    if card.get('categoryKey') not in {'characterquest', 'datescene'}:
        return False
    record = labels.get(str(card.get('characterBookId')), {})
    identity = card.get('iconCharacterId')
    if not record or record['characterId'] != identity or record['bookNum'] != card.get('characterBookNum'):
        return False
    card['characterDisplayName'] = record['name']
    if card['categoryKey'] == 'datescene':
        card['displayTitle'] = f"{record['name']}\u3000{card['title']}"
    return True
