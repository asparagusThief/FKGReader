"""Build catalog labels from official master data, never from filename ranges."""
from __future__ import annotations
import csv
import io
from .catalog import category_label, group_key
ALBUM_TYPES = {'CATEGORY_ID_EVENT_SCRIPT_LIST': (1, 'eventstory'), 'CATEGORY_ID_NORMAL_SCRIPT_LIST': (2, 'mainstory'), 'CATEGORY_ID_CONTORY_SCRIPT_LIST': (3, 'countrystory'), 'CATEGORY_ID_WHALE_SCRIPT_LIST': (4, 'whale'), 'CATEGORY_ID_LOTUS_LAKE_LIST': (5, 'lotuslake'), 'CATEGORY_ID_GIRL_DUNGEON_LIST': (6, 'girldungeon'), 'CATEGORY_ID_GIGANTIC_PEST_LIST': (7, 'giganticpest'), 'CATEGORY_ID_THREE_FLOWERS_TRIP_LIST': (8, 'threeflowers'), 'CATEGORY_ID_LIMIT_MISSION_LIST': (9, 'limitmission'), 'CATEGORY_ID_PASSING_GIRL_DUNGEON_LIST': (10, 'passinggirl'), 'CATEGORY_ID_MEMORY_STORY_DUNGEON_LIST': (11, 'memory'), 'CATEGORY_ID_ANCIENT_KNIGHT_DUNGEON_LIST': (12, 'ancientknight'), 'CATEGORY_ID_BLOOM_STORY_DUNGEON_LIST': (13, 'bloom'), 'CATEGORY_ID_ZAHRA_AL_AMAL_DUNGEON_LIST': (14, 'zahra'), 'CATEGORY_ID_SLEEPY_PRINCESS_QUEST_DUNGEON_LIST': (15, 'sleepyprincess')}
STORY_TYPES = {'STORY_TYPE_EVENT_STAGE_START': (2, 'ステージ開始'), 'STORY_TYPE_CHARACTER_QUEST_START': (3, 'クエスト開始'), 'STORY_TYPE_CHARACTER_QUEST_CLEAR': (4, 'クエストクリア'), 'STORY_TYPE_QUEST_CLEAR': (5, 'クエスト完了'), 'STORY_TYPE_EVENT_STAGE_CLEAR': (6, 'ステージクリア'), 'STORY_TYPE_BATTLE': (7, '戦闘')}
CSV_COLUMNS = {'masterStory': ['id', 'storyType', 'value'], 'masterAlbumDungeon': ['id', 'albumType', 'dungeonGroupId', 'dungeonTitle', 'dungeonBunnerUrl', 'dungeonId', 'storyId', 'storyValue', 'storyTitle', 'dungeonOrderNum', 'visibleAlbumStartDate', 'onBannerText'], 'masterCharacterQuest': ['id', 'name', 'description', 'unused', 'orderNum', 'nextId', 'questItemGroupId', 'characterId', 'characterBookId', 'questTypeId', 'levelNum', 'repeatStageFlag'], 'masterCharacterBook': ['enableFlag', 'enableValue', 'bookId', 'name', 'bookNum', 'languageOfFlower'], 'masterSpotStory': ['id', 'storyId', 'order', 'titleName', 'characterId', 'spotType', 'termsType', 'termsValue']}
SPOT_LOCATIONS = {0: 'どこでも出現', 1: 'ホーム', 2: 'キャラ', 3: 'ショップ', 4: '交換所'}

def field(row, name, default=None):
    return row.get(name, row.get('_' + name, default))

def integer(value, default=None):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def table_rows(value, name):
    if value is None:
        return []
    if isinstance(value, str):
        columns = CSV_COLUMNS[name]
        return [dict(zip(columns, row)) for row in csv.reader(io.StringIO(value)) if row]
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        raise ValueError(f'{name} must be an array, object or CSV string')
    result = []
    for row in value:
        if isinstance(row, dict):
            result.append(row)
        elif isinstance(row, list):
            result.extend(table_rows(row, name))
    return result

def build_metadata(payload):
    if not isinstance(payload, dict):
        raise ValueError('Master data must be a JSON object')
    source = payload.get('tables', payload)
    if not isinstance(source, dict):
        raise ValueError('tables must be an object')
    rows = {key: table_rows(source.get(key), key) for key in CSV_COLUMNS}
    if not rows['masterStory'] and (not rows['masterAlbumDungeon']) and (not rows['masterSpotStory']):
        raise ValueError("No masterStory, masterAlbumDungeon or masterSpotStory found. Use the game's master data export script.")
    definitions = payload.get('definitions') or {}
    if not isinstance(definitions, dict):
        definitions = {}
    album_defs = definitions.get('albumTypes') or {}
    story_defs = definitions.get('storyTypes') or {}
    if not isinstance(album_defs, dict) or not isinstance(story_defs, dict):
        raise ValueError('Type mappings in definitions must be objects')
    albums = {integer(album_defs.get(name), default): (key, category_label(key)) for name, (default, key) in ALBUM_TYPES.items()}
    phases = {integer(story_defs.get(name), default): label for name, (default, label) in STORY_TYPES.items()}
    quest_start = integer(story_defs.get('STORY_TYPE_CHARACTER_QUEST_START'), 3)
    quest_clear = integer(story_defs.get('STORY_TYPE_CHARACTER_QUEST_CLEAR'), 4)
    quests = {integer(field(row, 'id')): row for row in rows['masterCharacterQuest'] if integer(field(row, 'id'), 0) > 0}
    books = {integer(field(row, 'bookId')): row for row in rows['masterCharacterBook'] if integer(field(row, 'bookId'), 0) > 0}
    quest_sequences, first_quest_ids = ({}, {})
    for index, row in sorted(enumerate(rows['masterCharacterQuest']), key=lambda pair: (integer(field(pair[1], 'orderNum'), 0), pair[0])):
        quest_id, book_id = (integer(field(row, 'id')), integer(field(row, 'characterBookId')))
        quest_sequences[quest_id] = index
        first_quest_ids.setdefault(book_id, quest_id)
    updates, warnings = ({}, [])
    for row in rows['masterStory']:
        story_id = integer(field(row, 'id'), 0)
        if story_id <= 0:
            continue
        kind, value = (integer(field(row, 'storyType')), integer(field(row, 'value')))
        item = {'storyType': kind, 'value': value, 'phase': phases.get(kind, ''), 'isCharacterQuest': kind in (quest_start, quest_clear), 'metadataSources': ['masterStory']}
        if kind in (quest_start, quest_clear):
            item.update(category=category_label('characterquest'), categoryKey='characterquest', categorySource='masterStory.storyType', phaseOrder=0 if kind == quest_start else 1)
            quest = quests.get(value)
            if quest:
                title = str(field(quest, 'name', '') or '').strip()
                book_id = integer(field(quest, 'characterBookId'))
                item.update(questId=value, characterBookId=book_id, characterId=integer(field(quest, 'characterId')), group=str(field(books.get(book_id, {}), 'name', '') or ''), characterBookNum=integer(field(books.get(book_id, {}), 'bookNum')), characterGroupOrder=first_quest_ids.get(book_id), questSequence=quest_sequences.get(value), order=integer(field(quest, 'orderNum'), 0), questTypeId=integer(field(quest, 'questTypeId')))
                item['metadataSources'].append('masterCharacterQuest')
                if item['group']:
                    item['metadataSources'].append('masterCharacterBook')
                if title:
                    item.update(title=title, titleSource='masterCharacterQuest.name')
            else:
                warnings.append(f'Story {story_id} is a character quest, but title data for quest {value} is missing')
        updates[str(story_id)] = item
    album_groups = {}
    for index, row in enumerate(rows['masterAlbumDungeon']):
        story_id = integer(field(row, 'storyId'), 0)
        if story_id <= 0:
            continue
        item = updates.setdefault(str(story_id), {'metadataSources': []})
        album_type = integer(field(row, 'albumType'))
        category = albums.get(album_type)
        if item.get('categoryKey') != 'characterquest':
            if category:
                item.update(categoryKey=category[0], category=category[1], categorySource='masterAlbumDungeon.albumType')
            else:
                warnings.append(f'Story {story_id} has an unknown album type: {album_type}')
            group = str(field(row, 'dungeonTitle', '') or '').strip()
            if group:
                item['group'] = group
            item.update(groupId=str(field(row, 'dungeonGroupId', '') or ''), dungeonId=integer(field(row, 'dungeonId')), albumId=integer(field(row, 'id')), albumSequence=index, dungeonOrderNum=integer(field(row, 'dungeonOrderNum'), 0), order=index)
            album_groups.setdefault(group_key(item), {'groupOrder': integer(field(row, 'dungeonOrderNum'), 0), 'groupBannerId': integer(field(row, 'dungeonId')), 'bannerPath': str(field(row, 'dungeonBunnerUrl', '') or '').strip(), 'bannerText': str(field(row, 'onBannerText', '') or '').strip()})
        title = str(field(row, 'storyTitle', '') or '').strip()
        if title and (not item.get('title') or item.get('categoryKey') != 'characterquest'):
            item.update(title=title, titleSource='masterAlbumDungeon.storyTitle')
        item['albumType'] = album_type
        item.setdefault('metadataSources', []).append('masterAlbumDungeon')
    for item in updates.values():
        if item.get('categorySource') == 'masterAlbumDungeon.albumType':
            item.update(album_groups.get(group_key(item), {}))
    for row in rows['masterSpotStory']:
        story_id = integer(field(row, 'storyId'), 0)
        if story_id <= 0:
            continue
        item = updates.setdefault(str(story_id), {'metadataSources': []})
        if item.get('categoryKey') not in (None, 'unknown', 'spotstory'):
            warnings.append(f'花トーク {story_id} conflicts with the existing category; keeping the existing category')
            continue
        spot_id = integer(field(row, 'id'))
        spot_type = integer(field(row, 'spotType'))
        item.update(categoryKey='spotstory', category=category_label('spotstory'), categorySource='masterSpotStory.storyId', group=category_label('spotstory'), spotStoryId=spot_id, spotOrder=integer(field(row, 'order'), spot_id), spotType=spot_type, spotLocation=SPOT_LOCATIONS.get(spot_type, ''), order=integer(field(row, 'order'), spot_id))
        title = str(field(row, 'titleName', '') or '').strip()
        if title:
            item.update(title=title, titleSource='masterSpotStory.titleName')
        item['metadataSources'].append('masterSpotStory')
    if not updates:
        raise ValueError('No valid story IDs found')
    return {'updates': updates, 'tables': {key: len(value) for key, value in rows.items()}, 'warnings': warnings}

def merge_metadata(existing, updates):
    """Partial imports must not erase a title/category learned from other tables."""
    merged = {key: dict(value) for key, value in existing.items()}
    for key, update in updates.items():
        item = merged.setdefault(key, {})
        sources = set(item.get('metadataSources', [])) | set(update.get('metadataSources', []))
        keep_quest = item.get('isCharacterQuest') and update.get('isCharacterQuest') is not False
        keep_title = keep_quest and item.get('titleSource') == 'masterCharacterQuest.name' and (update.get('titleSource') == 'masterAlbumDungeon.storyTitle')
        keep_spot = item.get('categoryKey') == 'spotstory' and update.get('categorySource') != 'masterSpotStory.storyId'
        for name, value in update.items():
            if keep_spot and name in ('category', 'categoryKey', 'categorySource', 'title', 'titleSource', 'group', 'order', 'phase', 'groupId', 'dungeonId', 'albumId', 'albumSequence', 'bannerPath', 'bannerText'):
                continue
            if keep_quest and update.get('categorySource') == 'masterAlbumDungeon.albumType' and (name in ('category', 'categoryKey', 'categorySource', 'group', 'order', 'groupOrder', 'groupId', 'groupBannerId', 'dungeonId', 'albumId', 'albumSequence', 'dungeonOrderNum', 'bannerPath', 'bannerText')):
                continue
            if keep_title and name in ('title', 'titleSource'):
                continue
            if name != 'metadataSources' and value is not None and (value != ''):
                item[name] = value
        item['metadataSources'] = sorted(sources)
    return merged
