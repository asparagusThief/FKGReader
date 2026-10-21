"""Optional History icons from original names and explicit master/portrait joins."""
from collections import defaultdict
import json
from pathlib import Path
import re
from .date_master import validate_date_master

class SpeakerIconIndex:

    def __init__(self, books, characters=(), source_books=()):
        self.names, self.models = (defaultdict(set), defaultdict(set))
        self.portrait_names = defaultdict(set)
        self.model_names = defaultdict(set)
        source_names = {row.get('bookId'): row.get('name') for row in source_books}
        self.source_names, self.book_models = (source_names, defaultdict(set))
        for book in books.values():
            name, identity = (book.get('name'), book.get('characterId'))
            if isinstance(name, str) and name.strip() and (type(identity) is int) and (0 < identity <= 999999):
                name = name.strip()
                self.names[name].add(identity)
                self.portrait_names[name].add(identity)
                plain = re.sub('(?:\\([^()]+\\)|（[^（）]+）)+$', '', name).strip()
                if plain:
                    self.portrait_names[plain].add(identity)
                self.models[identity].add(identity)
        for row in characters:
            book = books.get(str(row.get('bookId')), {})
            identity, base = (row.get('characterId'), row.get('baseCharacterId'))
            if type(identity) is int and book.get('characterId') == base and (type(base) is int) and (0 < base <= 999999):
                self.models[identity].add(base)
            if type(identity) is int and isinstance(source_names.get(row.get('bookId')), str):
                self.model_names[identity].add(source_names[row['bookId']].strip())
                if 0 < identity <= 999999999:
                    self.book_models[row['bookId']].add(identity)
        for model, names in self.model_names.items():
            if model not in self.models:
                targets = set().union(*(self.names.get(name, set()) for name in names))
                if len(targets) == 1:
                    self.models[model].update(targets)

    @classmethod
    def load(cls, data_root: Path, books):
        path = data_root / 'fkg-date-master-recovered.json'
        if not path.is_file():
            return cls(books)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Speaker character master is too large')
        payload = validate_date_master(json.loads(path.read_text(encoding='utf-8-sig')))
        return cls(books, payload['masterDateCharacters'], payload['masterCharacterBook'])

    def portrait_for_term(self, row, resolve_portrait):
        """A special term with no square icon can use its own exported art."""
        if row.get('category') != 'character' or not row['id'].startswith('character:') or len(row.get('personIds', [])) != 1:
            return None
        for book_id in row.get('bookIds', []):
            if self.source_names.get(book_id) not in row['jaForms']:
                continue
            for model in sorted(self.book_models.get(book_id, set())):
                portrait = resolve_portrait(model)
                if portrait:
                    return portrait
        return None

    def for_portraits(self, payload, resolve_icon):
        """Resolve each explicit name/file pair separately from story-wide names."""
        bindings = defaultdict(list)
        for command in payload['script']['commands']:
            if command['op'] == 'story_chara' and len(command.get('args', [])) >= 2:
                file, name = command['args'][:2]
                bindings[file].append(name)
            elif command['op'] == 'chara_motion':
                for row in command.get('data', {}).get('list', []):
                    if row.get('fileName') and isinstance(row.get('name'), str):
                        bindings[row['fileName']].append(row['name'])
        result = {}
        for file, names in bindings.items():
            match = re.fullmatch('(?:character/(?:s|stand|stand_m)/)?bustup_([0-9]{6,9})(?:_[A-Za-z0-9]+)*', file)
            if not match:
                continue
            explicit = self.models.get(int(match[1]), set())
            candidates = explicit & set.intersection(*(self.portrait_names.get(name.strip(), set()) for name in names))
            if len(candidates) == 1:
                icon = resolve_icon(next(iter(candidates)))
                if icon:
                    result[file] = icon
        return result

    def for_story(self, payload, resolve_icon):
        commands = payload['script']['commands']
        bindings = defaultdict(set)

        def bind(name, file):
            if not isinstance(name, str) or not isinstance(file, str):
                return
            match = re.fullmatch('(?:character/(?:s|stand|stand_m)/)?bustup_([0-9]{6,9})(?:_[A-Za-z0-9]+)*', file)
            if match:
                bindings[name.strip()].update(self.models.get(int(match[1]), {None}))
        for command in commands:
            if command['op'] == 'story_chara' and len(command.get('args', [])) >= 2:
                file, name = command['args'][:2]
                bind(name, file)
            elif command['op'] == 'chara_motion':
                for row in command.get('data', {}).get('list', []):
                    bind(row.get('name'), row.get('fileName'))
        result = {}
        story = payload['story']
        for command in commands:
            raw_name = command.get('name') if command['op'] == 'mess' else None
            if not isinstance(raw_name, str) or not raw_name.strip() or re.search('[?？■▓]', raw_name):
                continue
            name = raw_name.strip()
            candidates = self.names.get(name, set())
            explicit = bindings.get(name, set())
            if explicit:
                candidates = self.portrait_names.get(name, set()) & explicit if len(explicit) == 1 else set()
            elif story.get('iconCharacterId') in self.portrait_names.get(name, set()) and story.get('iconCharacterId') in self.names.get(story.get('characterName') or story.get('group'), set()):
                candidates = {story['iconCharacterId']}
            if len(candidates) != 1:
                continue
            icon = resolve_icon(next(iter(candidates)))
            if icon:
                result[raw_name] = icon
        return result
