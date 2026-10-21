"""Image-bound, optional card transcriptions. Original art/master stay intact."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
KIND = 'fkg-messagecard-ocr'

def attach_transcriptions(entries, data_root, asset_root=None):
    """Fail closed per record on a changed PNG, master record or transcription."""
    path = Path(data_root) / 'messagecard-ocr.json'
    summary = {'transcribed': 0, 'decorative': 0, 'rejected': 0}
    issues = []
    if not path.is_file():
        return (summary, issues)
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Card transcription catalog is too large')
        catalog = json.loads(path.read_text(encoding='utf-8-sig'))
        if catalog.get('kind') != KIND or catalog.get('schemaVersion') != 1 or (not isinstance(catalog.get('cards'), dict)):
            raise ValueError('Unsupported card transcription catalog')
    except (OSError, ValueError, AttributeError) as error:
        return (summary, [f'Could not load image card transcriptions: {error}'])
    asset_root = Path(asset_root if asset_root is not None else Path(data_root) / 'messagecard-assets').resolve()
    for identity, row in catalog['cards'].items():
        try:
            entry = entries.get(identity)
            if not entry or not isinstance(row, dict) or entry['messageCard']['messageType'] != 2:
                raise ValueError('Unknown/non-image card')
            relative = entry['messageCard']['textImagePath']
            image = asset_root / relative
            if row.get('imagePath') != relative or row.get('originalRevision') != entry['revision'] or (not image.resolve().is_relative_to(asset_root)) or (image.stat().st_size > 8 * 1024 * 1024) or (hashlib.sha256(image.read_bytes()).hexdigest() != row.get('imageSha256')):
                raise ValueError('Original image/master revision changed')
            if row.get('classification') == 'emoticon_only':
                summary['decorative'] += 1
                continue
            text = row.get('text')
            if row.get('classification') != 'transcribed' or not isinstance(text, str) or (not text.strip()) or (len(text) > 20000) or re.search('[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]', text) or (hashlib.sha256(text.encode()).hexdigest() != row.get('textSha256')):
                raise ValueError('Invalid image transcription')
            evidence = {key: row[key] for key in ('imagePath', 'imageSha256', 'originalRevision', 'textSha256')}
            evidence.update(method=catalog.get('method', 'visual transcription'), notes=catalog.get('notes', ''))
            entry['messageCard'] = entry['messageCard'] | {'text': text.replace('%user%', '団長'), 'textTemplate': text, 'ocr': evidence, 'hasUserPlaceholder': '%user%' in text}
            summary['transcribed'] += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            summary['rejected'] += 1
            issues.append(f'Skipped stale or invalid image card transcription: {identity}')
    return (summary, issues)
