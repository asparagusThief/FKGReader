"""Decode game scripts without executing them or depending on the game client."""
from __future__ import annotations
import gzip
import json
import re
import zlib
from collections import Counter
from .script_compat import apply_compatibility

def decode_resource(data: bytes) -> str:
    if data.startswith(b'\x1f\x8b'):
        data = gzip.decompress(data)
    elif len(data) >= 2 and data[0] == 120 and ((data[0] * 256 + data[1]) % 31 == 0):
        data = zlib.decompress(data)
    return data.decode('utf-8-sig')

def records(text: str):
    """CSV-like game syntax also supports single-quoted, multiline JSON fields.

    Preserve JSON escaping and embedded commas. A plain split(',') would corrupt
    movement lists and choices, and a line-by-line parser would lose continuations.
    """
    text = text.replace('\t', '').replace('\r\n', '\n').replace('\r', '\n')
    command_names = '|'.join((re.escape(name) for name in KNOWN))
    text = re.sub('(?m)^[ \'\\"]+(?=(?:' + command_names + '),)', '', text)
    starts_command = re.compile('[ ]*(?:' + command_names + '|#[A-Za-z0-9_]+),')
    fields, field = ([], [])
    quote = None
    line, record_line = (1, 1)
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == '\n' and starts_command.match(text, i + 1):
                fields.append(''.join(field))
                if any((part.strip() for part in fields)):
                    yield (record_line, fields)
                fields, field, quote = ([], [], None)
                line += 1
                record_line = line
                i += 1
                continue
            if char == '\\' and i + 1 < len(text):
                field.extend((char, text[i + 1]))
                line += text[i + 1] == '\n'
                i += 2
                continue
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    field.append(char)
                    i += 2
                    continue
                look = i + 1
                while look < len(text) and text[look] == ' ':
                    look += 1
                if look == len(text) or text[look] in ',\n':
                    quote = None
                    i += 1
                    continue
            field.append(char)
        elif char in '"\'' and fields and (not ''.join(field).strip()):
            quote = char
            field = []
        elif char == ',':
            fields.append(''.join(field))
            field = []
        elif char == '\n':
            fields.append(''.join(field))
            if any((part.strip() for part in fields)):
                yield (record_line, fields)
            fields, field = ([], [])
            record_line = line + 1
        else:
            field.append(char)
        line += char == '\n'
        i += 1
    fields.append(''.join(field))
    if any((part.strip() for part in fields)):
        yield (record_line, fields)

def number(value: str, default=0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def visible_text(value: str) -> str:
    value = re.sub('<br\\s*/?>', '\n', value, flags=re.I)
    value = re.sub('<[^>]*>', '', value)
    return value.replace('\\n', '\n').replace('\\r', '')
KNOWN = {'mess', 'background', 'bgm', 'se', 'story_chara', 'story_chara_exit', 'chara_motion', 'emotion', 'effect', 'wait', 'choice', 'answer', 'message_window', 'message_window_position', 'overlay_effect', 'story_chara_effect', 'full_skip_dialog', 'image', 'image_delete', 'movie', 'movie_delete'}
JSON_OPS = {'chara_motion', 'emotion', 'choice', 'overlay_effect'}

def parse_script(text: str, *, compatibility=True) -> dict:
    commands, warnings = ([], [])
    refs = {kind: set() for kind in ('character', 'background', 'bgm', 'se', 'voice', 'image', 'movie')}
    counts = Counter()
    for line, row in records(text):
        op = row[0].strip()
        if op.startswith('#'):
            commands.append({'op': 'label', 'label': op, 'line': line})
            continue
        args = row[1:]
        command = {'op': op, 'args': args, 'line': line}
        counts[op] += 1
        if op not in KNOWN:
            warnings.append({'line': line, 'message': f'Unsupported or misspelled source command: {op[:80]}'})
        if op in JSON_OPS:
            try:
                command['data'] = json.loads(args[0])
            except (IndexError, ValueError) as error:
                warnings.append({'line': line, 'message': f'Could not parse {op} arguments: {error}'})
                command['data'] = {}
        if op == 'mess':
            command.update(name=args[0] if args else '', text=visible_text(args[1] if len(args) > 1 else ''), voice=args[2].strip() if len(args) > 2 else '')
            if command['voice']:
                refs['voice'].add(command['voice'])
        if op == 'story_chara' and args:
            refs['character'].add(args[0].strip())
        if op == 'chara_motion':
            for item in command.get('data', {}).get('list', []):
                if item.get('fileName'):
                    refs['character'].add(item['fileName'])
        if op in ('background', 'bgm', 'se', 'image', 'movie') and args and args[0].strip():
            refs[op].add(args[0].strip())
        commands.append(command)
    if compatibility:
        commands, recovered = apply_compatibility(text, commands, visible_text)
        warnings.extend(recovered)
        counts = Counter((command['op'] for command in commands if command['op'] != 'label'))
        refs['voice'].update((command['voice'] for command in commands if command['op'] == 'mess' and command.get('voice')))
    return {'commands': commands, 'warnings': warnings, 'counts': dict(counts), 'references': {kind: sorted(values) for kind, values in refs.items()}}
