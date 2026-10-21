"""Local resource parsing and validation; no network fetching."""
from __future__ import annotations
import re
import zlib
MAX_DOWNLOAD = 4 * 1024 * 1024
MAX_SCRIPT = 16 * 1024 * 1024

def validate_story(data):
    if not data or len(data) > MAX_DOWNLOAD:
        raise ValueError('Empty or oversized story response')
    if data.startswith(b'\x1f\x8b'):
        decoder = zlib.decompressobj(31)
    elif len(data) >= 2 and data[0] == 120 and ((data[0] * 256 + data[1]) % 31 == 0):
        decoder = zlib.decompressobj()
    else:
        raise ValueError('Response is not a compressed game story')
    decoded = decoder.decompress(data, MAX_SCRIPT + 1)
    if len(decoded) > MAX_SCRIPT or not decoder.eof:
        raise ValueError('Corrupt or oversized compressed story')
    text = decoded.decode('utf-8-sig')
    if not re.search('(?m)^\\s*[\'\\"]?(?:mess|background|story_chara|bgm|se|choice|movie|image|wait),', text):
        raise ValueError('Decoded response contains no recognizable story commands')
    return text
