"""Read-only labels for stable translation IDs, never name/Chinese-text guesses."""
import json
from pathlib import Path
import re
TERM_ID = re.compile('[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]{1,160}\\Z')

def term_label(row):
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or (not TERM_ID.fullmatch(row['id'])):
        return None
    forms = row.get('jaForms')
    if forms is None and isinstance(row.get('sourceForm'), str):
        forms = [row['sourceForm']]
    default = row.get('zhDefault')
    if not isinstance(forms, list) or not forms or len(forms) > 30 or any((not isinstance(value, str) or not 0 < len(value) <= 200 for value in forms)) or (not isinstance(default, str)) or (not 0 < len(default) <= 500):
        return None
    category = row.get('category', 'other')
    label = {'id': row['id'], 'jaForms': forms, 'zhDefault': default, 'category': category if isinstance(category, str) and len(category) <= 40 else 'other'}
    books = row.get('bookIds')
    if label['category'] == 'character' and row['id'].startswith('character:') and isinstance(books, list) and (0 < len(books) <= 200) and all((type(book) is int and 0 < book < 10 ** 10 for book in books)):
        label['bookIds'] = list(dict.fromkeys(books))
        people = row.get('personIds')
        if isinstance(people, list) and len(people) == 1 and (type(people[0]) is int) and (people[0] > 0):
            label['personIds'] = list(people)
    return label

def term_character_icon(row, books, resolve_icon):
    """Join explicit book IDs and exact Japanese labels, never Chinese names.

    Shared character terms include multiple costumes. Prefer only the book
    whose full official name matches a source form (normally the base version),
    not the first available costume or an arbitrary same-name character.
    """
    if row.get('category') != 'character' or not row['id'].startswith('character:'):
        return None
    candidates = set()
    related = []
    for book_id in row.get('bookIds', []):
        book = books.get(str(book_id), {})
        identity = book.get('characterId')
        if book.get('name') in row['jaForms'] and type(identity) is int and (0 < identity <= 999999):
            candidates.add(identity)
        if type(identity) is int and 0 < identity <= 999999 and isinstance(book.get('name'), str) and (re.sub('(?:\\([^()]+\\)|（[^（）]+）)+$', '', book['name']).strip() in row['jaForms']) and (type(book.get('bookNum')) is int) and (book['bookNum'] > 0):
            related.append(book)
    if not candidates and related and (len(row.get('personIds', [])) == 1):
        first_number = min((book['bookNum'] for book in related))
        candidates = {book['characterId'] for book in related if book['bookNum'] == first_number}
    return resolve_icon(next(iter(candidates))) if len(candidates) == 1 else None

class TranslationTerms:

    def __init__(self, root=None, additional_roots=()):
        self.root = Path(root).resolve() if root else None
        self.roots = tuple(dict.fromkeys(([self.root] if self.root else []) + [Path(path).resolve() for path in additional_roots]))
        self._stamp, self._rows = (None, {})

    def read(self, path):
        if not any((path.resolve().is_relative_to(root) for root in self.roots)) or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Invalid term metadata file')
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def registry(self):
        if self.root is None:
            return {}
        try:
            path = self.root / 'term-registry.json'
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
            if stamp != self._stamp:
                payload = self.read(path)
                if payload.get('kind') != 'fkg-term-registry' or not isinstance(payload.get('terms'), list):
                    raise ValueError('Invalid term registry')
                rows = [term_label(row) for row in payload['terms'][:10000]]
                self._rows = {row['id']: row for row in rows if row}
                self._stamp = stamp
            return self._rows
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def for_story(self, bundle, payload):
        rows = {identity: row for identity, row in self.registry().items() if identity in bundle['terms']}
        identity = bundle.get('storyId')
        if type(identity) is int and identity > 0:
            slug, term_scope = (f'story-{identity:06d}', str(identity))
        elif isinstance(identity, str) and (re.fullmatch('date:[0-9]{6}:100', identity) or re.fullmatch('message:[1-9][0-9]{0,9}', identity)):
            slug = term_scope = identity.replace(':', '-')
        else:
            return rows
        embedded = bundle.get('localTermInfo', {})
        if isinstance(embedded, dict):
            for key, item in list(embedded.items())[:10000]:
                row = term_label(item)
                if row and key == row['id'] and key.startswith(f'local:{term_scope}:') and (key in bundle['terms']) and (row['zhDefault'] == bundle['terms'][key]):
                    rows[key] = row
        category = payload['story'].get('categoryKey', '')
        if isinstance(category, str) and re.fullmatch('[a-z][a-z0-9_-]*', category):
            for root in self.roots:
                try:
                    output = self.read(root / 'outputs' / category / (slug + '.zh-Hans.json'))
                    source = output.get('source', {})
                    if source.get('storyId') != bundle['storyId'] or source.get('scriptRevision') != bundle['scriptRevision'] or (bundle.get('packageId') and output.get('packageId') != bundle['packageId']):
                        continue
                    for item in output.get('localTerms', [])[:10000]:
                        row = term_label(item)
                        if row and row['id'].startswith(f'local:{term_scope}:') and (row['id'] in bundle['terms']):
                            rows[row['id']] = row
                    break
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    continue
        return rows
