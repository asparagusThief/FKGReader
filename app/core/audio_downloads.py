"""Local resource parsing and validation; no network fetching."""
from __future__ import annotations
import hashlib
import re
import struct
MAX_AUDIO = 32 * 1024 * 1024
HEADER_LIMIT = 256 * 1024
AUDIO_KINDS = ('bgm', 'se', 'voice')

def audio_path(kind, reference):
    if kind not in AUDIO_KINDS or not isinstance(reference, str):
        raise ValueError('Unsupported audio kind/reference')
    name = reference.removesuffix('.mp3')
    if not 0 < len(name) <= 240 or not re.fullmatch('[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', name):
        raise ValueError('Unsupported audio reference; no guessed path')
    if kind == 'bgm':
        if '/' in name:
            raise ValueError('BGM must be an explicit logical filename')
        return f'assets/bgm/{name}.mp3'
    if kind == 'se':
        return f'assets/sound/{name}.mp3'
    parent, _, filename = name.rpartition('/')
    variant = 'f' if '_forever' in name else 'c'
    digest = hashlib.md5(filename.encode('ascii')).hexdigest()
    return f'assets/voice/{variant}/' + (parent + '/' if parent else '') + digest + '.mp3'

def _frame(data, offset):
    if offset + 4 > len(data):
        return None
    a, b, c, _ = data[offset:offset + 4]
    version, layer, rate = (b >> 3 & 3, b >> 1 & 3, c >> 2 & 3)
    index = c >> 4
    if a != 255 or b & 224 != 224 or version == 1 or (layer != 1) or (rate == 3) or (index in {0, 15}):
        return None
    rates = [44100, 48000, 32000]
    sample_rate = rates[rate] // (1 if version == 3 else 2 if version == 2 else 4)
    bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320] if version == 3 else [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    length = (144 if version == 3 else 72) * bitrates[index] * 1000 // sample_rate + (c >> 1 & 1)
    return (length, version, sample_rate)

def validate_mp3(data):
    """Reject HTML/errors/ID3-only/truncation: require two complete MP3 frames."""
    if not 8 <= len(data) <= MAX_AUDIO:
        raise ValueError('Empty/oversized MP3')
    offset = 0
    if data.startswith(b'ID3'):
        if len(data) < 10 or data[3] not in {2, 3, 4} or any((byte & 128 for byte in data[6:10])):
            raise ValueError('Invalid ID3 header')
        size = sum((byte << shift for byte, shift in zip(data[6:10], (21, 14, 7, 0))))
        offset = 10 + size + (10 if data[3] == 4 and data[5] & 16 else 0)
    for position in range(offset, min(len(data) - 4, offset + 4096)):
        first = _frame(data, position)
        if first:
            next_position = position + first[0]
            second = _frame(data, next_position)
            if second and second[1:] == first[1:] and (next_position + second[0] <= len(data)):
                return {'sampleRate': first[2]}
    raise ValueError('Response is not a supported complete MP3 audio stream')

def validate_file(path):
    size = path.stat().st_size
    if not 8 <= size <= MAX_AUDIO:
        raise ValueError('Empty/oversized audio file')
    with path.open('rb') as source:
        header = source.read(HEADER_LIMIT)
        validate_audio(header + source.read() if header.startswith(b'RIFF') else header)
    return size

def validate_audio(data):
    """Some original .mp3 URLs actually contain PCM/float RIFF WAVE files."""
    if not data.startswith(b'RIFF'):
        return validate_mp3(data)
    if not 44 <= len(data) <= MAX_AUDIO or data[8:12] != b'WAVE' or int.from_bytes(data[4:8], 'little') + 8 != len(data):
        raise ValueError('Incomplete/invalid RIFF WAVE audio')
    position, fmt, sizes = (12, None, [])
    while position + 8 <= len(data):
        kind, length = (data[position:position + 4], int.from_bytes(data[position + 4:position + 8], 'little'))
        start, end = (position + 8, position + 8 + length)
        if end > len(data):
            raise ValueError('Truncated WAVE chunk')
        if kind == b'fmt ':
            if fmt is not None or length < 16:
                raise ValueError('Invalid WAVE format chunk')
            fmt = struct.unpack_from('<HHIIHH', data, start)
            if fmt[0] == 65534:
                if length < 40 or int.from_bytes(data[start + 16:start + 18], 'little') < 22:
                    raise ValueError('Invalid extensible WAVE format')
                subtype = data[start + 24:start + 40]
                if subtype[2:] != bytes.fromhex('000000001000800000aa00389b71'):
                    raise ValueError('Unsupported WAVE subtype')
                fmt = (int.from_bytes(subtype[:2], 'little'),) + fmt[1:]
        elif kind == b'data':
            sizes.append(length)
        position = end + (length & 1)
    if position != len(data) or fmt is None or len(sizes) != 1:
        raise ValueError('Missing/invalid WAVE chunks')
    tag, channels, rate, byte_rate, align, bits = fmt
    if tag not in {1, 3} or not 1 <= channels <= 8 or (not 1 <= rate <= 384000) or (bits not in ({8, 16, 24, 32} if tag == 1 else {32, 64})) or (align != channels * bits // 8) or (byte_rate != rate * align) or (not sizes[0]) or sizes[0] % align:
        raise ValueError('Invalid/unsupported WAVE samples')
    return {'sampleRate': rate, 'format': 'wav', 'channels': channels}
