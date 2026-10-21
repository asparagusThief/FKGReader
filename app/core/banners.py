"""Resolve official banner references to local images; never load remote UI URLs."""
from __future__ import annotations
from .runtime_data import title_table, audio_aliases
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlsplit
from .catalog import ALBUM_BANNER_DIRECTORIES, FLAT_ALBUMS
GAME_ASSET_BASE = ''
GAME_HOSTS = set(title_table('bannerHosts'))

def normalize_banner_path(reference):
    if not isinstance(reference, str) or not reference.strip():
        return None
    value = reference.strip().replace('\\', '/')
    parts = urlsplit(value)
    if parts.scheme and (parts.scheme not in {'http', 'https'} or parts.hostname not in GAME_HOSTS):
        return None
    if parts.username or parts.password or (parts.netloc and parts.hostname not in GAME_HOSTS):
        return None
    path = unquote(parts.path)
    if '/assets/' in path:
        path = path.split('/assets/', 1)[1]
    elif path.startswith('assets/'):
        path = path[len('assets/'):]
    else:
        path = path.lstrip('/')
    segments = path.split('/')
    if any((segment in {'', '.', '..'} or any((char in segment for char in (':', '\x00', '\\', '%'))) for segment in segments)):
        return None
    if not path.startswith('images/album/') or PurePosixPath(path).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        return None
    return path

def group_banner_path(item):
    if item.get('categoryKey') in FLAT_ALBUMS:
        return None
    path = normalize_banner_path(item.get('bannerPath'))
    if not path:
        return None
    category = item.get('categoryKey')
    old = 'images/album/dungeon/'
    if category in ALBUM_BANNER_DIRECTORIES and path.startswith(old):
        return 'images/album/banner/' + ALBUM_BANNER_DIRECTORIES[category] + path[len(old):]
    return path

def main_part_banner_path(item, variant='dmm'):
    """SceneWorkRoom.Album.createMainCategory uses maingroup + group ID.

    ResourceURL.dmmOrGeneralAlbumBannerPath adds the edition directory. This
    is a separate parent banner, not the first chapter's dungeonBunnerUrl.
    """
    if item.get('categoryKey') != 'mainstory' or variant not in {'dmm', 'general'}:
        return None
    key = item.get('partKey')
    if not isinstance(key, str):
        return None
    match = re.fullmatch('mainstory-part:([1-9][0-9]*)', key)
    if not match:
        return None
    return f'images/album/banner/{variant}/maingroup{match.group(1)}.png'

def is_banner_image(data):
    return data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP')
