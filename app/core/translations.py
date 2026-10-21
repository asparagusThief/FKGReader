"""Optional, revision-bound reading text; never replace catalog or game data."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import re
from .translation_terms import TranslationTerms, term_character_icon
from .script_compat import choice_field_id
TOKEN = re.compile('\\{\\{term:([A-Za-z0-9_.:-]+)\\}\\}')
RUNTIME = re.compile('%[A-Za-z][A-Za-z0-9_]*%')

def body_fields(payload):
    """Use the same line-based identities as the independent source exporter."""
    fields = {}
    for command in payload['script']['commands']:
        line = command['line']
        if command['op'] == 'mess':
            if command.get('name'):
                fields[f'mess:{line}:name'] = command['name']
            fields[f'mess:{line}:text'] = payload['messageCard']['textTemplate'] if payload.get('messageCard') else command.get('text', '')
        elif command['op'] == 'choice':
            for index, item in enumerate(command.get('data', {}).get('list', [])):
                fields[choice_field_id(command, index)] = item['text']
    return fields

def validate_bundle(bundle, payload):
    if bundle.get('kind') != 'fkg-reader-translation' or type(bundle.get('schemaVersion')) is not int or bundle['schemaVersion'] != 1 or (bundle.get('targetLanguage') != 'zh-Hans') or (type(bundle.get('storyId')) is not type(payload['story']['id'])) or (bundle['storyId'] != payload['story']['id']) or (bundle.get('scriptRevision') != payload['revision']) or (bundle.get('status') != 'machine-draft'):
        raise ValueError('Translation does not match this story/revision')
    fields, entries, terms = (body_fields(payload), bundle.get('entries'), bundle.get('terms'))
    if not fields or not isinstance(entries, dict) or set(fields) != set(entries) or (not isinstance(terms, dict)):
        raise ValueError('Translation body fields are incomplete or unexpected')
    if any((not isinstance(value, str) or not value.strip() for value in terms.values())):
        raise ValueError('Invalid translation term defaults')
    for identity, original in fields.items():
        row = entries[identity]
        if not isinstance(row, dict) or row.get('source') != original or (not isinstance(row.get('template'), str)):
            raise ValueError(f'Translation source differs: {identity}')
        template = row['template']
        if bool(original.strip()) != bool(template.strip()):
            raise ValueError(f'Translation body is empty or adds text: {identity}')
        tokens = TOKEN.findall(template)
        if set(tokens) - set(terms) or '{{' in TOKEN.sub('', template) or '}}' in TOKEN.sub('', template):
            raise ValueError(f'Invalid translation term reference: {identity}')
        if Counter(RUNTIME.findall(template)) != Counter(RUNTIME.findall(original)):
            raise ValueError(f'Translation runtime placeholders differ: {identity}')
    return bundle

def compile_translation(source, translated, payload):
    """Called only after rich source/sidecar validation. Keep stable term tokens."""
    if source['source'] != translated['source'] or source['packageId'] != translated['packageId'] or source['source']['storyId'] != payload['story']['id'] or (source['source']['scriptRevision'] != payload['revision']):
        raise ValueError('Source package is not the current local story')
    originals = {row['entryId']: row for row in source['entries'] if row['locator']['area'] in {'script', 'messageCard'}}
    rows = {row['entryId']: row for row in translated['entries']}
    entries = {identity: {'source': row['source'], 'template': rows[identity]['template']} for identity, row in originals.items()}
    used = {identity for row in entries.values() for identity in TOKEN.findall(row['template'])}
    defaults = {term['id']: term.get('zhDefault') or term['jaForms'][0] for term in source['terms']}
    defaults.update({term['id']: term['zhDefault'] for term in translated.get('localTerms', [])})
    bundle = {'schemaVersion': 1, 'kind': 'fkg-reader-translation', 'targetLanguage': 'zh-Hans', 'status': 'machine-draft', 'storyId': payload['story']['id'], 'scriptRevision': payload['revision'], 'packageId': source['packageId'], 'entries': entries, 'terms': {identity: defaults[identity] for identity in sorted(used)}}
    return validate_bundle(bundle, payload)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate translation JSON key')
        result[key] = value
    return result

class TranslationCatalog:

    def __init__(self, root, term_root=None, additional_term_roots=()):
        self.root = Path(root).resolve()
        self.term_catalog = TranslationTerms(term_root, additional_term_roots)

    def terms(self, books=None, resolve_icon=None, resolve_term_portrait=None):
        rows = list(self.term_catalog.registry().values())
        if books is None or resolve_icon is None:
            return rows
        result = []
        for row in rows:
            icon = term_character_icon(row, books, resolve_icon)
            item = row | {'characterIcon': icon}
            if not icon and resolve_term_portrait:
                portrait = resolve_term_portrait(row)
                if portrait:
                    item['characterPortrait'] = portrait
            result.append(item)
        return result

    def for_story(self, payload):
        identity = payload['story']['id']
        if type(identity) is int and identity > 0:
            relative = Path('stories') / f'story-{identity:06d}.json'
        elif isinstance(identity, str) and re.fullmatch('date:[0-9]{6}:100', identity):
            relative = Path('dates') / ('date-' + identity.split(':')[1] + '-100.json')
        elif isinstance(identity, str) and re.fullmatch('message:[1-9][0-9]{0,9}', identity):
            relative = Path('messagecards') / ('message-' + identity.split(':')[1] + '.json')
        else:
            return None
        path = self.root / relative
        try:
            if not path.resolve().is_relative_to(self.root) or path.stat().st_size > 16 * 1024 * 1024:
                return None
            bundle = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object)
            validate_bundle(bundle, payload)
            info = self.term_catalog.for_story(bundle, payload)
            return bundle | {'termInfo': info} if info else bundle
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None
