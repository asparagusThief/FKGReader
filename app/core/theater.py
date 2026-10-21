"""Official theater catalog: scripts keep their story IDs; media has its own IDs.

The cached client's createFlowerKnightTheaterCategory includes types 1 and 3,
and hides groups below 69 except 65. Its video route changes .swf to .mp4 and
ResourceURL.videoPath prefixes assets/video/. HTML fortune and swan mini-games
are not videos and must never be embedded/executed by the offline reader.
"""
from __future__ import annotations
from .runtime_data import title_table, audio_aliases
import hashlib
import json
from pathlib import Path
import re
import zlib
from .banners import normalize_banner_path
from .scripts import parse_script
from .story_downloads import validate_story, MAX_DOWNLOAD
KIND = 'fkg-theater-master'
GROUP_TITLES = title_table('theater')

def positive(value):
    if type(value) is not int or not 0 < value < 10 ** 10:
        raise ValueError('Invalid theater ID')
    return value

def build_theater_master(payload):
    if not isinstance(payload, dict):
        raise ValueError('Theater master must be an object')
    source = payload.get('tables', payload)
    if not isinstance(source, dict):
        raise ValueError('Theater tables must be an object')
    rows = source.get('masterAlbumTheater')
    groups = source.get('masterAlbumTheaterGroup')
    if not isinstance(rows, list) or not isinstance(groups, list) or len(rows) > 10000 or (len(groups) > 10000):
        raise ValueError('Export masterAlbumTheater and masterAlbumTheaterGroup from the game')
    normalized_groups, normalized_rows = ({}, {})
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('Invalid theater group')
        identity = positive(group.get('id'))
        banner = normalize_banner_path(group.get('bannerUrl'))
        if identity in normalized_groups or not banner or type(group.get('orderNum')) is not int:
            raise ValueError('Invalid or duplicate theater group')
        normalized_groups[identity] = {'id': identity, 'bannerUrl': banner, 'orderNum': group['orderNum']}
        title = group.get('title')
        if isinstance(title, str) and title.strip():
            normalized_groups[identity]['title'] = title.strip()[:500]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid theater row')
        identity, group_id = (positive(row.get('id')), positive(row.get('groupId')))
        if identity in normalized_rows or group_id not in normalized_groups:
            raise ValueError('Duplicate theater row or missing group')
        title, url = (row.get('title'), row.get('url', ''))
        if not isinstance(title, str) or not title.strip() or (not isinstance(url, str)) or (len(url) > 1000):
            raise ValueError('Invalid theater title or resource')
        kind, order = (row.get('type'), row.get('orderNum'))
        if type(kind) is not int or kind not in range(1, 7) or type(order) is not int:
            raise ValueError('Invalid theater type or order')
        story_id, swan = (row.get('storyID', 0), row.get('swanID', 0))
        if type(story_id) is not int or type(swan) is not int or story_id < 0 or (swan < 0):
            raise ValueError('Invalid theater script or mini-game ID')
        if kind == 3:
            positive(story_id)
        normalized_rows[identity] = {'id': identity, 'groupId': group_id, 'title': title.strip(), 'type': kind, 'orderNum': order, 'url': url, 'storyID': story_id, 'swanID': swan}
    return {'kind': KIND, 'schemaVersion': 1, 'masterAlbumTheater': list(normalized_rows.values()), 'masterAlbumTheaterGroup': list(normalized_groups.values())}

def visible_rows(master):
    return [row for row in master['masterAlbumTheater'] if row['type'] in (1, 3) and (row['groupId'] >= 69 or row['groupId'] == 65)]

def video_path(row):
    if row['type'] != 1 or row['swanID'] or row['url'].startswith('html/'):
        return None
    if not re.fullmatch('[A-Za-z0-9_-]+(?:\\.swf|\\.mp4)?', row['url']):
        return None
    return 'assets/video/' + re.sub('(?:\\.swf|\\.mp4)?$', '.mp4', row['url'], count=1)

def script_path(story_id):
    digest = hashlib.md5(f'story_{positive(story_id):06d}'.encode('ascii')).hexdigest()
    return f'assets/event/story/{digest}.bin'

def is_video(path):
    try:
        size, boxes, offset = (path.stat().st_size, set(), 0)
        with path.open('rb') as stream:
            while offset < size:
                if size - offset < 8:
                    return False
                stream.seek(offset)
                header = stream.read(8)
                length, kind = (int.from_bytes(header[:4], 'big'), header[4:8])
                minimum = 8
                if length == 1:
                    length = int.from_bytes(stream.read(8), 'big')
                    minimum = 16
                elif length == 0:
                    length = size - offset
                if length < minimum or offset + length > size:
                    return False
                boxes.add(kind)
                offset += length
        return {b'ftyp', b'moov', b'mdat'} <= boxes
    except OSError:
        return False

def attach_theater(entries, cards, story_root, data_root):
    target = data_root / 'theater-master.json'
    summary = {'indexed': 0, 'stories': 0, 'videos': 0, 'interactive': 0, 'missingStories': 0}
    if not target.is_file():
        return (summary, [])
    try:
        if target.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Theater master is too large')
        master = build_theater_master(json.loads(target.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return (summary, [f'Could not load theater catalog: {error}'])
    groups = {row['id']: row for row in master['masterAlbumTheaterGroup']}
    rows = visible_rows(master)
    summary['indexed'] = len(rows)
    labels = {}
    for row in rows:
        labels.setdefault(row['groupId'], []).append(row['title'])
    for sequence, row in enumerate(rows):
        group = groups[row['groupId']]
        label = group.get('title') or GROUP_TITLES.get(group['bannerUrl'])
        label = label or (labels[group['id']][0] if len(labels[group['id']]) == 1 else '花騎士劇場')
        story_id = row['storyID'] if row['type'] == 3 else f"theater:{row['id']}"
        old = entries.get(story_id)
        card = {'id': story_id, 'logicalName': f'story_{story_id:06d}' if type(story_id) is int else f"theater_{row['id']:06d}", 'title': row['title'], 'displayTitle': row['title'], 'category': '花騎士劇場', 'categoryKey': 'theater', 'categorySource': 'masterAlbumTheater.type', 'titleSource': 'masterAlbumTheater.title', 'metadataSources': ['masterAlbumTheater', 'masterAlbumTheaterGroup'], 'group': label, 'groupLabel': label, 'groupKey': f"theater:{group['id']}", 'groupId': group['id'], 'groupOrder': group['orderNum'], 'order': sequence, 'theaterId': row['id'], 'bannerPath': group['bannerUrl'], 'bannerText': '', 'groupQualifier': '', 'phase': '', 'characters': [], 'preview': '', 'messageCount': 0, 'resourceKind': 'theater-media'}
        entry = {'card': card}
        if row['type'] == 3:
            summary['stories'] += 1
            path = old['path'] if old and 'path' in old else data_root / 'theater-assets' / script_path(story_id)
            try:
                if not path.resolve().is_relative_to(story_root.resolve()) and (not path.resolve().is_relative_to((data_root / 'theater-assets').resolve())):
                    raise ValueError('Theater script escaped its folder')
                if path.stat().st_size > MAX_DOWNLOAD:
                    raise ValueError('Theater script exceeds size limit')
                parsed = parse_script(validate_story(path.read_bytes()))
                messages = [cmd for cmd in parsed['commands'] if cmd['op'] == 'mess']
                card.update(resourceKind='theater-story', mediaKind='Story', messageCount=len(messages), characters=sorted({cmd['name'] for cmd in messages if cmd['name']}), preview=messages[0]['text'][:110] if messages else '')
                entry['path'] = path
            except (OSError, ValueError, UnicodeError, zlib.error):
                summary['missingStories'] += 1
                entry['theaterMedia'] = {'kind': 'missing-story', 'notice': 'Story file is not downloaded yet.'}
                card['mediaKind'] = 'Story · Not downloaded'
        else:
            asset = video_path(row)
            if asset:
                summary['videos'] += 1
                card['mediaKind'] = 'Video'
                entry['theaterMedia'] = {'kind': 'video', 'assetPath': asset}
            else:
                summary['interactive'] += 1
                card['mediaKind'] = 'Interactive · Game only'
                entry['theaterMedia'] = {'kind': 'interactive', 'notice': 'This interactive mini-game requires the original game. It is not a video.'}
        if 'theaterMedia' in entry:
            entry['revision'] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        entries[story_id] = entry
        cards[:] = [item for item in cards if item['id'] != story_id]
        cards.append(card)
    return (summary, [])
