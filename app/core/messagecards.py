"""Read exported card master content without account data or invented story IDs."""
from __future__ import annotations
import hashlib
import html
import json
import re
from pathlib import Path
IMAGE_BASE = 'assets/ui/characterPresent/nazunaMessage/'
GROUP_LABELS = {1: 'クリスマス', 2: 'バレンタイン', 3: '七夕'}
MAX_MASTER = 20 * 1024 * 1024

def positive(value):
    return type(value) is int and 0 < value < 10 ** 10

def image_path(filename):
    if not isinstance(filename, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}\\.png', filename):
        raise ValueError('Invalid message card image filename')
    return IMAGE_BASE + filename

def validate_master(payload):
    if not isinstance(payload, dict) or payload.get('kind') != 'fkg-messagecard-master' or payload.get('schemaVersion') != 1 or (payload.get('scope') != 'currently-loaded-master-messagecards'):
        raise ValueError('Unsupported メッセージカード master data')
    if payload.get('textImageBasePath') != IMAGE_BASE:
        raise ValueError('Card image folder does not match the original game')
    expected = {'messageGroups': {'CHRISTMAS': 1, 'VALENTINE': 2, 'TANABATA': 3}, 'messageTypes': {'TEXT': 1, 'IMAGE': 2}, 'characterTypes': {'NORMAL': 0, 'SPECIAL': 1}}
    if payload.get('definitions') != expected:
        raise ValueError('Card type definitions do not match the verified game version')
    for key in ('masterMessageCard', 'masterCharacters', 'masterCharacterBook'):
        if not isinstance(payload.get(key), list) or not 0 < len(payload[key]) <= 25000:
            raise ValueError(f'Invalid or empty {key}')
    ids = set()
    for row in payload['masterMessageCard']:
        if not isinstance(row, dict) or not positive(row.get('id')) or row['id'] in ids:
            raise ValueError('Duplicate or invalid card ID')
        ids.add(row['id'])
        if not positive(row.get('characterId')) or type(row.get('messageGroupId')) is not int or row['messageGroupId'] not in GROUP_LABELS:
            raise ValueError('Invalid card character or seasonal type')
        if type(row.get('samePersonID')) is not int or row['samePersonID'] < 0:
            raise ValueError('Original game samePersonID mapping is missing')
        if type(row.get('messageOrderNum')) is not int or row['messageOrderNum'] < 0:
            raise ValueError('Invalid card order')
        if type(row.get('messageType')) is not int or row['messageType'] not in (1, 2) or type(row.get('isSpecialCharacter')) is not int or (row['isSpecialCharacter'] not in (0, 1)):
            raise ValueError('Unsupported card type')
        for field, maximum in (('messageTitle', 500), ('message', 20000)):
            if not isinstance(row.get(field), str) or len(row[field]) > maximum:
                raise ValueError(f'Invalid card {field}')
        if not row['messageTitle'].strip() or (row['messageType'] == 1 and (not row['message'].strip())):
            raise ValueError('Text card is missing a title or body')
        if row['messageType'] == 2:
            image_path(row.get('messageTextImagePath'))
    for key, id_field in (('masterCharacters', 'characterId'), ('masterCharacterBook', 'bookId')):
        seen = set()
        for row in payload[key]:
            if not isinstance(row, dict) or not positive(row.get(id_field)) or row[id_field] in seen:
                raise ValueError(f'Duplicate or invalid {key} ID')
            seen.add(row[id_field])
    return payload

def read_master(path):
    if path.stat().st_size > MAX_MASTER:
        raise ValueError('Card master data is too large')
    return validate_master(json.loads(path.read_text(encoding='utf-8-sig')))

def plain_message(value):
    return html.unescape(re.sub('<br\\s*/?>', '\n', value, flags=re.I))

def build_cards(payload, icon_books=None):
    validate_master(payload)
    icon_books = icon_books or {}
    characters = {row['characterId']: row for row in payload['masterCharacters']}
    books = {row['bookId']: row for row in payload['masterCharacterBook']}
    people = {}
    for row in characters.values():
        book = books.get(row.get('bookId'))
        person = row.get('samePersonId')
        if book and positive(person) and isinstance(book.get('name'), str) and positive(book.get('bookNum')):
            people.setdefault(person, []).append((book, row))
    representatives = {person: min(rows, key=lambda pair: (pair[0]['bookNum'], pair[1].get('bookOrderNum') or 999, pair[1]['characterId'])) for person, rows in people.items()}
    entries = {}
    unresolved = set()
    for sequence, row in enumerate(payload['masterMessageCard']):
        char_id, person = (row['characterId'], row['samePersonID'])
        char = characters.get(char_id, {})
        direct_book = books.get(char.get('bookId'), {})
        group_book, group_char = representatives.get(person, (direct_book, char)) if person else (direct_book, char)
        group_name = group_book.get('name') or f'特殊カード（ID {char_id}）'
        name = direct_book.get('name') or f'キャラクター ID {char_id}'
        if not direct_book.get('name'):
            unresolved.add(char_id)
        book_num = group_book.get('bookNum')
        display_num = book_num if positive(book_num) and book_num < 1000000 else None
        group_label = f'No.{display_num}\u3000{group_name}' if display_num else group_name
        group_identity = f'person:{person}' if person else f'character:{char_id}'
        text = plain_message(row['message'])
        source_image = image_path(row['messageTextImagePath']) if row['messageType'] == 2 else None
        identity = f"message:{row['id']}"
        icon = icon_books.get(str(direct_book.get('bookId')), {}).get('characterId')
        group_icon = icon_books.get(str(group_book.get('bookId')), {}).get('characterId')
        icon = icon or (char_id if char_id <= 999999 and (not row['isSpecialCharacter']) else None)
        group_icon = group_icon or (group_char.get('characterId') if 0 < group_char.get('characterId', 0) <= 999999 else None)
        card = {'id': identity, 'resourceKind': 'messagecard', 'logicalName': f"messagecard_{row['id']}", 'title': row['messageTitle'], 'displayTitle': f"{name}\u3000{row['messageTitle']}", 'category': 'メッセージカード', 'categoryKey': 'messagecard', 'group': group_name, 'groupKey': f'messagecard:{group_identity}', 'groupLabel': group_label, 'characterName': name, 'characterId': char_id, 'characterBookId': direct_book.get('bookId'), 'characterBookNum': display_num, 'iconCharacterId': icon, 'groupIconCharacterId': group_icon, 'phase': GROUP_LABELS[row['messageGroupId']], 'messageGroupId': row['messageGroupId'], 'messageOrderNum': row['messageOrderNum'], 'messageSequence': sequence, 'messageSortOrder': row['messageOrderNum'] if row['isSpecialCharacter'] else char_id, 'messageId': row['id'], 'order': sequence, 'groupOrder': display_num or 10 ** 9 + char_id, 'messageCount': 1, 'characters': [name], 'preview': text[:110] or '文字画像カード', 'categorySource': 'masterMessageCardData.messageGroupId', 'titleSource': 'masterMessageCardData.messageTitle', 'metadataSources': ['masterMessageCardData', 'MasterCharacterList', 'MasterCharacterBook']}
        raw = {field: row.get(field) for field in ('id', 'characterId', 'messageGroupId', 'messageOrderNum', 'messageTitle', 'message', 'messageType', 'expressionNum', 'isSpecialCharacter', 'messageTextImagePath', 'cardDesignType')}
        entries[identity] = {'card': card, 'messageCard': {'text': text.replace('%user%', '団長'), 'textTemplate': text, 'rawMessage': row['message'], 'textImagePath': source_image, 'hasUserPlaceholder': '%user%' in row['message'], 'groupId': row['messageGroupId'], 'messageType': row['messageType']}, 'revision': hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    summary = {'available': len(entries), 'text': sum((r['messageType'] == 1 for r in payload['masterMessageCard'])), 'image': sum((r['messageType'] == 2 for r in payload['masterMessageCard'])), 'groups': {GROUP_LABELS[k]: sum((r['messageGroupId'] == k for r in payload['masterMessageCard'])) for k in GROUP_LABELS}, 'unresolvedCharacters': len(unresolved), 'scope': payload['scope']}
    return (entries, summary)

def load_cards(data_root, icon_books=None, card_asset_root=None):
    path = data_root / 'messagecard-master.json'
    if not path.is_file():
        return ({}, {'available': 0}, [])
    try:
        entries, summary = build_cards(read_master(path), icon_books)
        from .messagecard_ocr import attach_transcriptions
        ocr_summary, issues = attach_transcriptions(entries, data_root, card_asset_root)
        summary['imageTranscriptions'] = ocr_summary
        return (entries, summary, issues)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return ({}, {'available': 0}, [f'Could not load メッセージカード: {error}'])

def card_script(entry):
    info, card = (entry['messageCard'], entry['card'])
    text = info['text'] or '文字画像カード (see original image)'
    return {'commands': [{'op': 'mess', 'line': 1, 'args': [], 'name': card['characterName'], 'text': text, 'voice': ''}], 'references': {'image': [info['textImagePath']] if info['textImagePath'] else []}, 'warnings': []}
