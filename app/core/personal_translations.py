"""User-owned translation overlays. Never write game data or AI sidecars."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4
from .translations import TOKEN, RUNTIME, body_fields, unique_object
KIND = 'fkg-reader-personal-translations'
MAX_BYTES = 20 * 1024 * 1024
FIELD = re.compile('(?:mess:[0-9]+:(?:text|name)|choice:[0-9]+:(?:[A-Za-z0-9_.-]{1,80}|option:[0-9]+):text)')
STORY = re.compile('(?:[1-9][0-9]{0,9}|date:[0-9]{6}:100|message:[1-9][0-9]{0,9})')
TERM = re.compile('[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]{1,160}')

class EditConflict(ValueError):
    pass

def validate_entry(row):
    if not isinstance(row, dict):
        raise ValueError('Invalid revision entry')
    story = row.get('storyId')
    if type(story) not in (str, int) or not STORY.fullmatch(str(story)):
        raise ValueError('Invalid revision story ID')
    identity, source, template = (row.get(key) for key in ('identity', 'source', 'template'))
    if not isinstance(identity, str) or not FIELD.fullmatch(identity):
        raise ValueError('Only dialogue and choice fields can be revised')
    for value in (source, template):
        if not isinstance(value, str) or not value.strip() or len(value) > 30000 or re.search('[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]', value):
            raise ValueError('Use 1–30,000 characters of non-empty text')
    terms = row.get('terms', {})
    if not isinstance(terms, dict) or len(terms) > 500 or any((not isinstance(key, str) or not TERM.fullmatch(key) or (not isinstance(value, str)) or (not value.strip()) or (len(value) > 500) or re.search('[\\x00-\\x1f\\x7f]', value) for key, value in terms.items())):
        raise ValueError('Invalid term defaults')
    used = set(TOKEN.findall(template))
    if used - set(terms) or '{{' in TOKEN.sub('', template) or '}}' in TOKEN.sub('', template):
        raise ValueError('Unknown or incomplete glossary link')
    if Counter(RUNTIME.findall(source)) != Counter(RUNTIME.findall(template)):
        raise ValueError('Keep the original runtime placeholders (for example %user%)')
    revision = row.get('scriptRevision', '')
    if not isinstance(revision, str) or len(revision) > 200:
        raise ValueError('Invalid source revision')
    return {'storyId': str(story), 'identity': identity, 'source': source, 'template': template, 'terms': {key: terms[key] for key in sorted(used)}, 'scriptRevision': revision, 'updatedAt': str(row.get('updatedAt', ''))[:100]}

def validate_document(document):
    if not isinstance(document, dict) or document.get('kind') != KIND or type(document.get('schemaVersion')) is not int or (document['schemaVersion'] != 1) or (document.get('targetLanguage') != 'zh-Hans') or (not isinstance(document.get('entries'), list)) or (len(document['entries']) > 20000):
        raise ValueError('Not a supported personal revisions file')
    rows, keys = ([], set())
    for item in document['entries']:
        row = validate_entry(item)
        key = (row['storyId'], row['identity'])
        if key in keys:
            raise ValueError('Duplicate revision field')
        keys.add(key)
        rows.append(row)
    return rows

class PersonalTranslations:

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError('Personal revisions file is too large')
        return validate_document(json.loads(self.path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object))

    @staticmethod
    def document(rows):
        return {'kind': KIND, 'schemaVersion': 1, 'targetLanguage': 'zh-Hans', 'entries': rows}

    def export(self):
        with self.lock:
            return self.document(self._read())

    def _write(self, rows):
        document = self.document(rows)
        validate_document(document)
        data = json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8')
        if len(data) > MAX_BYTES:
            raise ValueError('Personal revisions exceed the 20 MB limit')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._atomic(self.path.with_suffix('.json.bak'), self.path.read_bytes())
        self._atomic(self.path, data)

    @staticmethod
    def _atomic(path, data):
        fd, temporary = tempfile.mkstemp(prefix='.personal-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def for_story(self, payload):
        try:
            with self.lock:
                rows = [row for row in self._read() if row['storyId'] == str(payload['story']['id'])]
            fields = body_fields(payload)
            active = {row['identity']: row for row in rows if fields.get(row['identity']) == row['source']}
            return {'entries': active, 'staleCount': len(rows) - len(active), 'versions': {row['identity']: row['updatedAt'] for row in rows}}
        except (OSError, ValueError, TypeError):
            return {'entries': {}, 'staleCount': 0, 'warning': 'Personal revisions could not be read. The file was not changed.'}

    def save(self, request, payload):
        identity = request.get('identity')
        if not isinstance(identity, str) or not FIELD.fullmatch(identity):
            raise ValueError('Invalid dialogue field')
        fields = body_fields(payload)
        if identity not in fields or fields[identity] != request.get('source'):
            raise EditConflict('The original line changed. Reopen the editor before saving.')
        with self.lock:
            rows = self._read()
            key = (str(payload['story']['id']), identity)
            old = next((row for row in rows if (row['storyId'], row['identity']) == key), None)
            if request.get('expectedUpdatedAt') != (old['updatedAt'] if old else None):
                raise EditConflict('This line was revised elsewhere. Reopen the editor to load the latest version.')
            if request.get('reset') is not True:
                row = validate_entry(request | {'storyId': key[0], 'scriptRevision': payload['revision'], 'updatedAt': datetime.now(timezone.utc).isoformat() + ':' + uuid4().hex[:8]})
            rows = [item for item in rows if (item['storyId'], item['identity']) != key]
            if request.get('reset') is not True:
                rows.append(row)
            self._write(rows)
            return self.for_story(payload)

    def import_document(self, document, replace=False, preview=False):
        incoming = validate_document(document)
        with self.lock:
            existing = self._read()
            merged = {(row['storyId'], row['identity']): row for row in existing}
            conflicts = sum(((row['storyId'], row['identity']) in merged for row in incoming))
            for row in incoming:
                key = (row['storyId'], row['identity'])
                if key not in merged or replace:
                    merged[key] = row | {'updatedAt': datetime.now(timezone.utc).isoformat() + ':' + uuid4().hex[:8]}
            if not preview:
                self._write(list(merged.values()))
            return {'total': len(incoming), 'added': len(incoming) - conflicts, 'conflicts': conflicts, 'replaced': conflicts if replace else 0, 'kept': 0 if replace else conflicts}
