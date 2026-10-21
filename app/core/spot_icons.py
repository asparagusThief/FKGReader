"""SpotStoryAlbumElement uses the literal pipe-separated master character IDs."""
from __future__ import annotations
import json
import re
from pathlib import Path

def spot_character_ids(value):
    if not isinstance(value, str):
        return []
    if value.strip() in {'', '-----'}:
        return []
    tokens = value.split('|')
    if len(tokens) > 12 or not all((re.fullmatch('[0-9]{6}', token) and int(token) > 0 for token in tokens)):
        raise ValueError('Unsupported spot-story character list')
    return [int(token) for token in tokens]

def build_spot_icon_index(master, character_master):
    if character_master.get('kind') != 'fkg-date-master' or character_master.get('schemaVersion') != 2:
        raise ValueError('Version 2 character/date master is required')
    rows = master.get('masterSpotStory')
    if not isinstance(rows, list) or not 0 < len(rows) <= 25000:
        raise ValueError('Missing masterSpotStory rows')
    books = {row['bookId']: row for row in character_master.get('masterCharacterBook', [])}
    characters = {row['characterId']: row for row in character_master.get('masterDateCharacters', [])}
    if not books or not characters:
        raise ValueError('Version 2 character/date master is required for explicit icon names')
    stories = {}
    for row in rows:
        story, number = (row.get('storyId'), row.get('id'))
        if type(story) is not int or story <= 0 or type(number) is not int or (number <= 0):
            raise ValueError('Invalid spot-story IDs')
        participants = []
        for character in spot_character_ids(row.get('characterId')):
            record = characters.get(character, {})
            book = books.get(record.get('bookId'), {})
            name = book.get('name')
            participants.append({'characterId': character, 'name': name if isinstance(name, str) and name.strip() else f'Character ID {character}'})
        item = {'spotStoryId': number, 'participants': participants}
        if str(story) in stories and stories[str(story)] != item:
            raise ValueError('Conflicting spot-story character mappings')
        stories[str(story)] = item
    return {'kind': 'fkg-spot-character-index', 'schemaVersion': 1, 'source': 'masterSpotStory.characterId', 'stories': stories}

def load_spot_icon_index(path: Path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Spot character index is too large')
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if payload.get('kind') != 'fkg-spot-character-index' or payload.get('schemaVersion') != 1 or payload.get('source') != 'masterSpotStory.characterId' or (not isinstance(payload.get('stories'), dict)):
        raise ValueError('Unsupported spot character index')
    for story, row in payload['stories'].items():
        if not re.fullmatch('[1-9][0-9]*', story) or not isinstance(row, dict):
            raise ValueError('Invalid spot-story entry')
        if type(row.get('spotStoryId')) is not int or row['spotStoryId'] <= 0:
            raise ValueError('Invalid spot-story number')
        participants = row.get('participants')
        if not isinstance(participants, list) or len(participants) > 12:
            raise ValueError('Invalid spot-story participants')
        for participant in participants:
            character = participant.get('characterId') if isinstance(participant, dict) else None
            if type(character) is not int or not 0 < character <= 999999:
                raise ValueError('Invalid spot-story icon character')
            if not isinstance(participant.get('name'), str) or not participant['name'].strip():
                raise ValueError('Invalid spot-story character name')
    return payload['stories']
