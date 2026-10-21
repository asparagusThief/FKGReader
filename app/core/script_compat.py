"""Hash-bound, reviewed compatibility rules. Original resource bytes stay intact."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

@lru_cache(maxsize=1)
def profiles():
    path = Path(__file__).resolve().parents[2] / 'content/catalog/script-compat-profiles.json'
    return json.loads(path.read_text(encoding='utf-8'))['profiles'] if path.is_file() else {}

def choice_field_id(command, index):
    """Display identity is not branch identity. Preserve legacy unique IDs."""
    items = command.get('data', {}).get('list', [])
    element = items[index]['element']
    elements = [item.get('element') for item in items if isinstance(item, dict)]
    if len(elements) != len(items) or any((not isinstance(value, str) for value in elements)):
        raise ValueError('Missing choice element')
    ambiguous = any((not value or elements.count(value) != 1 for value in elements))
    return f"choice:{command['line']}:option:{index}:text" if ambiguous else f"choice:{command['line']}:{element}:text"

def apply_compatibility(text, commands, visible_text):
    profile = profiles().get(hashlib.sha256(text.encode()).hexdigest())
    if not profile:
        return (commands, [])
    result = deepcopy(commands)
    by_line = {command['line']: command for command in result}
    for rule in profile['rules']:
        command = by_line.get(rule['line'])
        if not command or any((command.get(key) != value for key, value in rule['expected'].items())):
            raise ValueError('Reviewed source compatibility rule no longer matches')
        if 'targetExpected' in rule and by_line.get(rule['targetLine']) != rule['targetExpected']:
            raise ValueError('Reviewed continuation target no longer matches')
    notes = []
    for rule in profile['rules']:
        command = by_line[rule['line']]
        original = deepcopy(command)
        action = rule['action']
        command['sourceCompatibility'] = {'rule': action, 'original': original, 'reason': rule['reason'], 'storyId': profile['storyId']}
        if action in {'context', 'review'}:
            command['exportClassification'] = 'review_required' if action == 'review' else 'reviewed_nontext'
            if command['op'] in {'mess', 'chara_motion'}:
                command['op'] = 'source_note'
            if action == 'review':
                command['reviewText'] = visible_text(original['op'])
        elif action in {'narration', 'missing_mess_commas'}:
            body = original['op'][4:] if action == 'missing_mess_commas' else original['op']
            command.update(op='mess', args=['', body], name='', text=visible_text(body), voice='')
        elif action == 'merge_continuation':
            target = by_line[rule['targetLine']]
            previous = {key: target.get(key, '') for key in ('name', 'text', 'voice')}
            raw_body = target['args'][1]
            separator = '' if not raw_body or raw_body.endswith(('\\n', '\n')) else '\\n'
            raw_body += separator + original['op']
            target['args'][1] = raw_body
            target['text'] = visible_text(raw_body)
            if rule.get('takeVoice'):
                target['voice'] = original['args'][0].strip()
                target['args'] = target['args'][:2] + [target['voice']]
            target['sourceCompatibility'] = {'rule': action, 'previousMessage': previous, 'continuationLine': rule['line'], 'reason': rule['reason'], 'storyId': profile['storyId']}
            command['exportClassification'] = 'merged_continuation'
        elif action == 'choice_literal_newline':
            data = json.loads(original['args'][0], strict=False)
            if not isinstance(data, dict) or not isinstance(data.get('list'), list) or (not data['list']) or any((not isinstance(item.get('text'), str) or not isinstance(item.get('element'), str) for item in data['list'])):
                raise ValueError('Reviewed choice recovery is incomplete')
            command['data'] = data
        else:
            raise ValueError('Unknown compatibility action')
        notes.append({'line': rule['line'], 'code': 'source_compatibility', 'message': 'Read-only source compatibility: ' + rule['reason']})
    return (result, notes)
