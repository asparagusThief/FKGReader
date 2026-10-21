"""Read only available first-date files, in an independent ID namespace."""
from __future__ import annotations
import hashlib
import json
import re
import zlib
from pathlib import Path
from .scripts import visible_text
from .story_downloads import MAX_DOWNLOAD, validate_story

def load_dates(root: Path | None):
    entries, issues = ({}, [])
    summary = {'indexed': 0, 'available': 0, 'missing': 0, 'invalid': 0}
    manifest = root / 'date-manifest.json' if root else None
    if not manifest or not manifest.is_file():
        return (entries, summary, issues)
    try:
        if manifest.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Date scene manifest is too large')
        payload = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if payload.get('kind') != 'fkg-date-library' or payload.get('schemaVersion') != 1 or payload.get('scope') != 'all-age-first-date':
            raise ValueError('Unsupported all-ages first-date manifest')
        rows = payload.get('entries')
        if not isinstance(rows, dict):
            raise ValueError('Date scene manifest is missing entries')
    except (OSError, ValueError, AttributeError) as error:
        issues.append(f'Could not load date scene library: {error}')
        return (entries, summary, issues)
    summary['indexed'] = len(rows)
    resolved_root = root.resolve()
    for identity, row in rows.items():
        try:
            match = re.fullmatch('date:([0-9]{6}):100', identity)
            if not match or not isinstance(row, dict):
                raise ValueError('Invalid date scene ID')
            base = int(match[1])
            logical = f'dscene_{base:06d}'
            digest = hashlib.md5(logical.encode('ascii')).hexdigest()
            if base <= 0 or type(row.get('baseCharacterId')) is not int or row['baseCharacterId'] != base or (row.get('hash') != digest) or (row.get('logicalName') != logical) or (row.get('datePath') != f'event/dscene/{logical}'):
                raise ValueError('Date scene path does not match the character ID')
            name = row.get('characterName')
            book, book_num = (row.get('characterBookId'), row.get('characterBookNum'))
            if not isinstance(name, str) or not name.strip() or type(book) is not int or (book <= 0):
                raise ValueError('Character book mapping is missing')
            if type(book_num) is not int or book_num <= 0:
                raise ValueError('Character book number is missing')
            path = root / 'bin' / f'{digest}.bin'
            if not path.resolve().is_relative_to(resolved_root):
                raise ValueError('Date scene asset path is outside its folder')
            if not path.is_file():
                summary['missing'] += 1
                continue
            if path.stat().st_size > MAX_DOWNLOAD:
                raise ValueError('Date scene script is too large')
            text = validate_story(path.read_bytes())
            names = sorted(set(re.findall('^mess,([^,\\r\\n]+),', text, flags=re.M)))
            first_message = re.search('^mess,[^,\\r\\n]*,([^\\r\\n]+)', text, flags=re.M)
            preview = visible_text(first_message.group(1).rstrip(',') if first_message else '')[:110]
            card = {'id': identity, 'resourceKind': 'dscene', 'baseCharacterId': base, 'logicalName': logical, 'title': '好感度100%', 'displayTitle': f'{name}\u3000好感度100%', 'category': 'デートシーン', 'categoryKey': 'datescene', 'group': name, 'groupKey': f'datescene:{book}', 'groupLabel': f'No.{book_num}\u3000{name}', 'characterBookId': book, 'characterBookNum': book_num, 'phase': '好感度100%', 'phaseOrder': 0, 'order': book_num, 'groupOrder': book_num, 'categorySource': 'date-manifest.event/dscene', 'titleSource': 'game-date-menu', 'metadataSources': ['MasterCharacterList', 'MasterCharacterBook', 'EventScriptPath'], 'characters': names, 'preview': preview, 'messageCount': len(re.findall('^mess,', text, flags=re.M))}
            entries[identity] = {'card': card, 'path': path}
        except (OSError, ValueError, TypeError, KeyError, zlib.error):
            summary['invalid'] += 1
    summary['available'] = len(entries)
    if summary['invalid']:
        issues.append(f"Skipped {summary['invalid']} invalid date scene entries or scripts; original files were preserved.")
    return (entries, summary, issues)
