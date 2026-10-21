"""Japanese album labels and ordering verified against the original game cache.

Labels come from assets/images/album/banner, not translations of internal enum
names (LOTUS_LAKE, for example, actually means 水影の騎士). Category order is
SceneWorkRoom.Album.update's sequence, not enum values or alphabetic order.
"""
from __future__ import annotations
from .runtime_data import title_table, audio_aliases
import re
CATEGORIES = title_table('categories')
CATEGORY_ORDER = {key: index for index, key in enumerate(CATEGORIES)}
FLAT_ALBUMS = {'threeflowers', 'passinggirl', 'girldungeon', 'memory', 'sleepyprincess', 'spotstory'}
SOURCE_ORDER_ALBUMS = {'whale', 'lotuslake', 'ancientknight', 'bloom', 'zahra', 'countrystory', 'limitmission'}
CATEGORY_BANNER_FILES = title_table('categoryBannerFiles')
ALBUM_BANNER_DIRECTORIES = title_table('albumBannerDirectories')
BLOOM_BANNER_TITLES = title_table('bloom')
CHAPTER_BANNER_TITLES = title_table('chapter')
MAIN_PART_BANNER_TITLES = title_table('mainPart')

def number(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

def category_label(key, fallback='未分類'):
    return CATEGORIES.get(key, fallback)

def group_key(item):
    key = item.get('categoryKey', 'unknown')
    if key in FLAT_ALBUMS or key == 'unknown':
        return key
    if key in {'characterquest', 'datescene'}:
        identity = item.get('characterBookId') or item.get('group') or item.get('id')
    elif key == 'mainstory':
        identity = f"{item.get('groupId', '')}:{item.get('dungeonId') or item.get('group', '')}"
    elif key in {'eventstory', 'countrystory'}:
        identity = item.get('groupId') or item.get('group') or item.get('id')
    else:
        identity = item.get('dungeonId') or item.get('groupId') or item.get('group') or item.get('id')
    return f'{key}:{identity}'

def group_label(item):
    key = item.get('categoryKey', 'unknown')
    if key in FLAT_ALBUMS or key == 'unknown':
        return category_label(key)
    if key == 'datescene':
        return f"No.{item.get('characterBookNum')}\u3000{item.get('characterDisplayName') or item.get('group', '')}"
    if key == 'characterquest':
        book_number = number(item.get('characterBookNum'))
        name = item.get('characterDisplayName') or item.get('group') or category_label(key)
        return f'No.{book_number}\u3000{name}' if book_number > 0 else name
    if key == 'eventstory':
        name = item.get('group') or category_label(key)
        return re.sub('\\s*(?:\\((?:前半|後半|后半)\\)|（(?:前半|後半|后半)）)\\s*$', '', name).strip() or name
    if key in {'mainstory', 'bloom', 'lotuslake', 'zahra', 'ancientknight', 'limitmission', 'whale', 'countrystory'}:
        from .banners import group_banner_path
        banner = group_banner_path(item)
        if banner in CHAPTER_BANNER_TITLES:
            return CHAPTER_BANNER_TITLES[banner]
    return item.get('group') or category_label(key)

def game_sort_key(item):
    """Flatten album hierarchy without interleaving chapters/characters.

    Most album groups are descending IDs. Event banners use descending
    dungeonOrderNum; country banners use ascending dungeonId; limit mission
    banners use ascending dungeonOrderNum. The game's
    SortCallbackDataByMasterAlbumDungeonIdDesc is applied to child entries with
    no such property, so it keeps their master-table order rather than reversing
    them. The export preserves that order for the existing cached tables.
    """
    key = item.get('categoryKey', 'unknown')
    category = CATEGORY_ORDER.get(key, len(CATEGORY_ORDER))
    sequence = number(item.get('albumSequence'), number(item.get('order'), number(item.get('id'))))
    album_id = number(item.get('albumId'), sequence)
    dungeon = number(item.get('dungeonId'), number(item.get('groupId')))
    group_order = number(item.get('groupOrder'))
    if key == 'theater':
        return (category, group_order, group_key(item), number(item.get('order')), number(item.get('theaterId')))
    if key == 'messagecard':
        return (category, group_order, item['groupKey'], number(item.get('messageSortOrder')), number(item.get('messageSequence')), number(item.get('messageId')))
    if key == 'datescene':
        return (category, number(item.get('characterBookNum')), group_key(item), number(item.get('phaseOrder')), number(item.get('baseCharacterId')))
    if key == 'spotstory':
        return (category, number(item.get('spotStoryId'), number(item.get('id'))), number(item.get('id')))
    if key == 'characterquest':
        book_number = number(item.get('characterBookNum'))
        return (category, book_number <= 0, book_number if book_number > 0 else 0, group_key(item), number(item.get('order')), number(item.get('questSequence')), number(item.get('phaseOrder')), number(item.get('id')))
    if key == 'mainstory':
        return (category, -number(item.get('groupId')), -dungeon, -album_id, sequence, number(item.get('id')))
    if key == 'eventstory':
        return (category, -group_order, number(item.get('groupId')), group_key(item), -album_id, sequence, number(item.get('id')))
    if key in FLAT_ALBUMS:
        return (category, -album_id, sequence, number(item.get('id')))
    if key == 'countrystory':
        return (category, number(item.get('groupBannerId'), dungeon), group_key(item), sequence, number(item.get('id')))
    if key == 'limitmission':
        return (category, group_order, dungeon, group_key(item), sequence, number(item.get('id')))
    if key in SOURCE_ORDER_ALBUMS:
        return (category, -dungeon, group_key(item), sequence, number(item.get('id')))
    return (category, group_key(item), sequence, number(item.get('id')))
