"""Local resource parsing and validation; no network fetching."""
from __future__ import annotations
import json
import re
import zlib
MAX_JSON = 4 * 1024 * 1024
LEGACY_FOLDERS = ('s', 'cutin', 'stand', 'stand_m')

def load_character_resource_index(path):
    if not path.is_file():
        return ({}, set())
    if path.stat().st_size > MAX_JSON:
        raise ValueError('Scene character index too large')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('kind') != 'fkg-scene-character-index' or data.get('schemaVersion') != 1:
        raise ValueError('Invalid scene character index')
    aliases, ids = (data.get('aliases'), data.get('characterIds'))
    if not isinstance(aliases, dict) or not isinstance(ids, list) or any((not isinstance(k, str) or not re.fullmatch('[0-9]{6,9}', k) or (not isinstance(v, str)) or (not re.fullmatch('[0-9]{6,9}', v)) for k, v in aliases.items())) or any((not isinstance(k, str) or not re.fullmatch('[0-9]{6,9}', k) for k in ids)):
        raise ValueError('Invalid scene character IDs/aliases')
    return (aliases, set(ids))

def character_name(value):
    if not isinstance(value, str):
        raise ValueError('Invalid character reference')
    name = value.removesuffix('.png')
    explicit_legacy = False
    for prefix in (f'character/{folder}/' for folder in LEGACY_FOLDERS):
        if name.startswith(prefix):
            name = name[len(prefix):]
            explicit_legacy = True
            break
    if not re.fullmatch('[A-Za-z0-9_-]{1,120}', name) or not (explicit_legacy or name.startswith(('bustup_', 'cutin_', 'enemy_', 'stand_'))):
        raise ValueError('Unsupported character reference (no guessed resource path)')
    return name

def character_folder(value):
    for folder in LEGACY_FOLDERS:
        if value.startswith(f'character/{folder}/'):
            return folder
    name = character_name(value)
    return 'stand_m' if name.startswith('stand_m_') else 'stand' if name.startswith('stand_') else 'cutin' if name.startswith('cutin_') else 's'

def atlas_data(data, bundle=None, aliases=None):
    if not 0 < len(data) <= MAX_JSON:
        raise ValueError('Empty/oversized atlas JSON')
    if data.startswith(b'\x1f\x8b') or data[:1] == b'x':
        decoder = zlib.decompressobj(31 if data.startswith(b'\x1f\x8b') else 15)
        data = decoder.decompress(data, MAX_JSON + 1)
        if not decoder.eof or len(data) > MAX_JSON:
            raise ValueError('Corrupt/oversized compressed atlas')
    payload = json.loads(data.decode('utf-8-sig'))
    if not isinstance(payload, dict) or not isinstance(payload.get('frames'), dict) or (not isinstance(payload.get('meta'), dict)):
        raise ValueError('Not a game portrait atlas')
    frames, meta = (payload['frames'], payload['meta'])
    if not 0 < len(frames) <= 1000:
        raise ValueError('Invalid atlas frame count')
    sources = [frame for frame in frames.values() if isinstance(frame, dict) and frame.get('isSource') is True]
    if len(sources) != 1:
        raise ValueError('Atlas needs exactly one original body frame')
    size = meta.get('size', {})
    if any((type(size.get(k)) is not int or not 0 < size[k] <= 8192 for k in ('w', 'h'))):
        raise ValueError('Invalid atlas image dimensions')
    source_size = sources[0].get('sourceSize', {})
    if any((type(source_size.get(k)) is not int or not 0 < source_size[k] <= 8192 for k in ('w', 'h'))):
        raise ValueError('Invalid portrait canvas dimensions')
    expected_id = bundle.split('_')[1] if bundle else None
    expected_ids = {expected_id, (aliases or {}).get(expected_id, expected_id)} if expected_id else set()
    for name, frame in frames.items():
        canonical = character_name(name)
        if expected_ids and canonical.split('_')[1] not in expected_ids:
            raise ValueError('Atlas belongs to a different character')
        if not isinstance(frame, dict) or frame.get('rotated'):
            raise ValueError('Unsupported rotated/invalid atlas frame')
        rect = frame.get('frame', {})
        if any((type(rect.get(k)) is not int or rect[k] < (1 if k in {'w', 'h'} else 0) for k in ('x', 'y', 'w', 'h'))):
            raise ValueError('Invalid atlas rectangle')
        if rect['x'] + rect['w'] > size['w'] or rect['y'] + rect['h'] > size['h']:
            raise ValueError('Atlas rectangle exceeds image')
        destination = frame.get('spriteSourceSize', {})
        if any((type(destination.get(k)) is not int or not -8192 <= destination[k] <= 8192 for k in ('x', 'y'))) or any((type(destination.get(k)) is not int or not 0 < destination[k] <= 8192 for k in ('w', 'h'))):
            raise ValueError('Invalid atlas destination')
    return payload
