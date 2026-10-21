"""Story catalog, cache decoding, and optional official metadata import."""
from __future__ import annotations
import csv
import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from .scripts import decode_resource, parse_script, visible_text
from .metadata import build_metadata, merge_metadata
from .catalog import CATEGORY_ORDER, MAIN_PART_BANNER_TITLES, category_label, game_sort_key, group_key, group_label
from .date_library import load_dates
from .character_icons import load_icon_index, merge_icon_labels
from .messagecards import load_cards, card_script
from .spot_icons import load_spot_icon_index
from .speaker_icons import SpeakerIconIndex
from .character_labels import build_character_labels, load_character_labels, apply_character_label
from .search import normalize_search
from .theater import attach_theater, build_theater_master

def parse_story_identity(value):
    """Preserve legacy integer IDs; dates never become aliases of those IDs."""
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str):
        if re.fullmatch('[0-9]+', value) and int(value) > 0:
            return int(value)
        if re.fullmatch('date:[0-9]{6}:100', value):
            return value
        if re.fullmatch('message:[1-9][0-9]{0,9}', value) and int(value.split(':')[1]) < 10 ** 10:
            return value
        if re.fullmatch('theater:[1-9][0-9]{0,9}', value):
            return value
    raise ValueError('Invalid story ID')

class StoryLibrary:

    def __init__(self, story_root: Path, data_root: Path, date_root: Path | None=None, *, card_asset_root=None):
        self.story_root = story_root
        self.data_root = data_root
        self.date_root = date_root
        self.card_asset_root = card_asset_root
        self.issues = []
        self.date_summary = {}
        self.messagecard_summary = {}
        self.theater_summary = {}
        self.entries = {}
        self.cards = []
        self.metadata_file = data_root / 'story-metadata.json'
        self.metadata = {}
        self.lock = threading.RLock()
        self.refresh()

    def refresh(self):
        with self.lock:
            self.read.cache_clear()
            self.entries, self.cards = ({}, [])
            self.issues = []
            try:
                icon_books = load_icon_index(self.data_root / 'character-icon-index.json')
            except (OSError, ValueError, AttributeError) as error:
                icon_books = {}
                self.issues.append(f'Could not load character icon mappings: {error}')
            try:
                character_labels = load_character_labels(self.data_root / 'character-labels.json')
            except (OSError, ValueError, TypeError, AttributeError) as error:
                character_labels = {}
                self.issues.append(f'Could not load character display names: {error}')
            icon_books = merge_icon_labels(icon_books, character_labels)
            self.icon_books = icon_books
            try:
                self.speaker_icons = SpeakerIconIndex.load(self.data_root, icon_books)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                self.speaker_icons = SpeakerIconIndex(icon_books)
                self.issues.append(f'Could not load speaker icon mappings: {error}')
            try:
                spot_icons = load_spot_icon_index(self.data_root / 'spot-character-index.json')
            except (OSError, ValueError, AttributeError, TypeError) as error:
                spot_icons = {}
                self.issues.append(f'Could not load 花トーク icon mappings: {error}')
            if self.metadata_file.is_file():
                self.metadata = json.loads(self.metadata_file.read_text(encoding='utf-8'))
            manifest = self.story_root / 'story-manifest.csv'
            if not manifest.is_file():
                raise FileNotFoundError(f'Story manifest not found: {manifest}')
            with manifest.open(encoding='utf-8-sig', newline='') as source:
                rows = list(csv.DictReader(source))
            for row in rows:
                try:
                    story_id = int(row['storyId'])
                    logical_name = f'story_{story_id:06d}'
                    digest = hashlib.md5(logical_name.encode()).hexdigest()
                    path = self.story_root / 'bin' / f'{digest}.bin'
                    if not path.is_file():
                        continue
                    text = decode_resource(path.read_bytes())
                    meta = self.metadata.get(str(story_id), {})
                    names = sorted(set(re.findall('^mess,([^,\\r\\n]+),', text, flags=re.M)))
                    match = re.search('^mess,[^,\\r\\n]*,([^\\r\\n]+)', text, flags=re.M)
                    preview = visible_text(match.group(1).rstrip(',') if match else '')[:110]
                    card = {'id': story_id, 'logicalName': logical_name, 'title': meta.get('title') or f'Story {story_id:06d}', 'category': category_label(meta.get('categoryKey', 'unknown'), meta.get('category') or '未分類'), 'group': meta.get('group') or '', 'categoryKey': meta.get('categoryKey', 'unknown'), 'categorySource': meta.get('categorySource', ''), 'phase': meta.get('phase', ''), 'titleSource': meta.get('titleSource', ''), 'metadataSources': meta.get('metadataSources', []), 'order': meta.get('order', story_id), 'phaseOrder': meta.get('phaseOrder', 0), 'groupOrder': meta.get('groupOrder', 0), 'characters': names, 'preview': preview, 'messageCount': len(re.findall('^mess,', text, flags=re.M))}
                    for name in ('groupId', 'groupBannerId', 'dungeonId', 'albumId', 'albumSequence', 'dungeonOrderNum', 'characterId', 'characterBookId', 'characterBookNum', 'characterGroupOrder', 'questSequence', 'bannerPath', 'bannerText', 'spotStoryId', 'spotOrder', 'spotType', 'spotLocation'):
                        card[name] = meta.get(name)
                    if card['categoryKey'] == 'characterquest':
                        card['iconCharacterId'] = icon_books.get(str(card.get('characterBookId')), {}).get('characterId') or card.get('characterId')
                    if card['categoryKey'] == 'spotstory':
                        linked = spot_icons.get(str(story_id), {})
                        card['participants'] = linked.get('participants', []) if linked.get('spotStoryId') == card['spotStoryId'] else []
                    card['displayTitle'] = f"No.{card['spotStoryId']}\u3000{card['title']}" if card['categoryKey'] == 'spotstory' and card['spotStoryId'] is not None else card['title']
                    card['groupKey'] = group_key(card)
                    card['groupLabel'] = group_label(card)
                    self.entries[story_id] = {'card': card, 'path': path}
                    self.cards.append(card)
                except (ValueError, KeyError, OSError, UnicodeError):
                    continue
            self.theater_summary, theater_issues = attach_theater(self.entries, self.cards, self.story_root, self.data_root)
            self.issues.extend(theater_issues)
            dates, self.date_summary, date_issues = load_dates(self.date_root)
            self.entries.update(dates)
            for entry in dates.values():
                entry['card']['iconCharacterId'] = entry['card']['baseCharacterId']
            self.cards.extend((entry['card'] for entry in dates.values()))
            self.issues.extend(date_issues)
            messagecards, self.messagecard_summary, card_issues = load_cards(self.data_root, icon_books, self.card_asset_root)
            self.entries.update(messagecards)
            self.cards.extend((entry['card'] for entry in messagecards.values()))
            self.issues.extend(card_issues)
            for card in self.cards:
                if apply_character_label(card, character_labels):
                    card['groupLabel'] = group_label(card)
            part_labels = {}
            for card in self.cards:
                if card['categoryKey'] == 'mainstory':
                    card['partKey'] = f"mainstory-part:{card.get('groupId') or 'unassigned'}"
                    labels = part_labels.setdefault(card['partKey'], set())
                    match = re.search('第\\s*([0-9０-９]+)\\s*部', card.get('group') or '')
                    if match:
                        labels.add(f'第{int(match.group(1))}部')
            for card in self.cards:
                if card['categoryKey'] == 'mainstory':
                    labels = part_labels[card['partKey']]
                    card['partShortLabel'] = next(iter(labels)) if len(labels) == 1 else '名称未取得の部'
                    card['partLabel'] = MAIN_PART_BANNER_TITLES.get(card['partKey'], card['partShortLabel'])
            for card in self.cards:
                card['groupQualifier'] = ''
            self.cards.sort(key=game_sort_key)
            self.cards = [card for card in self.cards if card['categoryKey'] != 'unknown']

    def listing(self, query='', category='', offset=0, limit=80, ids=None, group='', part=''):
        with self.lock:
            cards = self.cards
            if ids is not None:
                cards = [item for item in cards if item['id'] in ids]
            if category:
                cards = [item for item in cards if category in (item['category'], item['categoryKey'])]
            if group:
                cards = [item for item in cards if item['groupKey'] == group]
            if part:
                cards = [item for item in cards if item.get('partKey') == part]
            if query:
                query = normalize_search(query)
                cards = [item for item in cards if query in normalize_search(' '.join((str(item['id']), item['logicalName'], item['title'], item['group'], item['category'], item['displayTitle'], item.get('groupLabel') or '', item.get('spotLocation') or '', item.get('partLabel') or '', item.get('partShortLabel') or '', f"No.{item['characterBookNum']}" if item.get('characterBookNum') is not None else '', item['preview'], ' '.join(item['characters']), ' '.join((person['name'] for person in item.get('participants', []))))))]
            return {'total': len(cards), 'offset': offset, 'items': cards[offset:offset + limit]}

    @lru_cache(maxsize=32)
    def read(self, story_id: int | str):
        story_id = parse_story_identity(story_id)
        entry = self.entries[story_id]
        if 'theaterMedia' in entry:
            return {'story': entry['card'], 'revision': entry['revision'], 'theaterMedia': entry['theaterMedia'], 'script': {'commands': [], 'references': {}, 'warnings': []}}
        if entry['card'].get('resourceKind') == 'messagecard':
            return {'story': entry['card'], 'revision': entry['revision'], 'script': card_script(entry), 'messageCard': entry['messageCard']}
        text = decode_resource(entry['path'].read_bytes())
        return {'story': entry['card'], 'revision': hashlib.sha256(text.encode()).hexdigest(), 'script': parse_script(text)}

    def metadata_summary(self):
        with self.lock:
            classified = sum((card['categoryKey'] != 'unknown' for card in self.cards))
            titled = sum((bool(card['titleSource']) for card in self.cards))
            categories = {}
            for card in self.cards:
                categories[card['category']] = categories.get(card['category'], 0) + 1
            return {'total': len(self.cards), 'classified': classified, 'titled': titled, 'unclassified': len(self.cards) - classified, 'untitled': len(self.cards) - titled, 'categories': categories}

    def category_options(self):
        with self.lock:
            counts = {}
            for card in self.cards:
                key = card['categoryKey']
                counts[key] = counts.get(key, 0) + 1
            return [{'key': key, 'label': category_label(key), 'count': counts[key]} for key in sorted(counts, key=lambda key: CATEGORY_ORDER.get(key, len(CATEGORY_ORDER)))]

    def groups(self, category=''):
        with self.lock:
            groups = {}
            for card in self.cards:
                if category and category not in (card['categoryKey'], card['category']):
                    continue
                entry = groups.setdefault(card['groupKey'], {'key': card['groupKey'], 'label': card['groupLabel'], 'qualifier': card.get('groupQualifier', ''), 'categoryKey': card['categoryKey'], 'count': 0, 'partKey': card.get('partKey'), 'partLabel': card.get('partLabel'), 'partShortLabel': card.get('partShortLabel'), 'bannerPath': card.get('bannerPath'), 'bannerText': card.get('bannerText'), 'characterName': card.get('characterDisplayName') or card.get('group'), 'characterBookNum': card.get('characterBookNum'), 'iconCharacterId': card.get('groupIconCharacterId', card.get('iconCharacterId')), 'directStoryId': card['id'] if card['categoryKey'] in {'datescene', 'theater'} else None, 'mediaKind': card.get('mediaKind')})
                entry['count'] += 1
                if entry['count'] > 1:
                    entry['directStoryId'] = None
                    entry['mediaKind'] = None
            return list(groups.values())

    def import_metadata(self, payload: dict):
        if payload.get('kind') == 'fkg-character-labels':
            result = build_character_labels(payload)
            with self.lock:
                target = self.data_root / 'character-labels.json'
                books = load_character_labels(target)
                result['books'] = books | result['books']
                self.data_root.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix('.json.pending')
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(target)
                self.refresh()
                return {'imported': len(payload['characters']), 'matched': sum((bool(card.get('characterDisplayName')) for card in self.cards))}
        if payload.get('kind') == 'fkg-messagecard-master':
            raise ValueError('Import card master data with prepare-messagecards.py instead of the story title importer.')
        if payload.get('kind') in {'fkg-date-master', 'fkg-date-library'}:
            raise ValueError('Date scenes load automatically from the configured date folder; no story master data import is needed.')
        theater_source = payload.get('tables', payload)
        result = None
        if isinstance(theater_source, dict) and 'masterAlbumTheater' in theater_source:
            theater = build_theater_master(payload)
            if any((theater_source.get(key) for key in ('masterStory', 'masterAlbumDungeon', 'masterSpotStory'))):
                result = build_metadata(payload)
            target = self.data_root / 'theater-master.json'
            self.data_root.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.json.pending')
            temporary.write_text(json.dumps(theater, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
            if not any((theater_source.get(key) for key in ('masterStory', 'masterAlbumDungeon', 'masterSpotStory'))):
                self.refresh()
                return {'imported': self.theater_summary['indexed'], 'matched': self.theater_summary['stories'] - self.theater_summary['missingStories']}
        result = result or build_metadata(payload)
        with self.lock:
            self.metadata = merge_metadata(self.metadata, result['updates'])
            self.data_root.mkdir(parents=True, exist_ok=True)
            temporary = self.metadata_file.with_suffix('.json.pending')
            temporary.write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.metadata_file)
            self.refresh()
            matched = len(set(map(str, self.entries)) & result['updates'].keys())
            return {'imported': len(result['updates']), 'matched': matched, 'tables': result['tables'], 'summary': self.metadata_summary(), 'warningCount': len(result['warnings']), 'warnings': result['warnings'][:30]}
