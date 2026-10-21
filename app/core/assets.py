"""Resolve only original game cache assets; no third-party asset bundle is used."""
from __future__ import annotations
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from statistics import median
from .scripts import decode_resource
from .catalog import CATEGORY_BANNER_FILES
from .banners import group_banner_path, main_part_banner_path, normalize_banner_path
from .character_icons import icon_hash, is_icon_image
from .messagecards import IMAGE_BASE
from .scene_downloads import LEGACY_FOLDERS, atlas_data, character_folder, character_name, load_character_resource_index
from .audio_downloads import audio_path, validate_file
from .resource_compat import BACKGROUND_ALIASES, audio_target
from .theater import is_video

def portrait_head_reference(frames):
    """Stable expression-patch reference, not inferred face/eye landmarks."""
    rectangles = set()
    for item in frames.values():
        if item.get('isSource'):
            continue
        crop, destination = (item.get('frame', {}), item.get('spriteSourceSize', {}))
        values = (destination.get('x'), destination.get('y'), crop.get('w'), crop.get('h'))
        if all((type(value) is int for value in values)) and values[2] > 0 and (values[3] > 0):
            x, y, width, height = values
            rectangles.add((x + width / 2, y + height / 2, width, height))
    if not rectangles:
        return None
    center_x, center_y, width, height = [median((row[index] for row in rectangles)) for index in range(4)]
    return {'x': center_x - width / 2, 'y': center_y - height / 2, 'w': width, 'h': height}

class AssetCatalog:

    def __init__(self, product_root: Path, banner_root: Path | None=None, icon_root: Path | None=None, card_root: Path | None=None, scene_root: Path | None=None, audio_root: Path | None=None, theater_root: Path | None=None):
        self.product_root = product_root
        self.root = product_root / 'assets'
        self.files = {}
        self.banner_root = banner_root
        self.icon_root = icon_root
        self.card_root = card_root
        self.scene_root = scene_root
        self.audio_root = audio_root
        self.theater_root = theater_root
        self.videos = {}
        self.card_images = {}
        self.icons = {}
        self.banners = {}
        self.banner_sizes = {}
        self.actors = {}
        self.backgrounds = {}
        self.audio = {kind: {} for kind in ('bgm', 'se', 'voice')}
        self.issues = []
        self.refresh()

    def register(self, path: Path) -> str:
        path = path.resolve()
        token = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        self.files[token] = path
        return f'/api/asset/{token}'

    def refresh(self):
        self.files.clear()
        self.banners.clear()
        self.banner_sizes.clear()
        self.icons.clear()
        self.card_images.clear()
        self.actors.clear()
        self.backgrounds.clear()
        for group in self.audio.values():
            group.clear()
        self.issues.clear()
        self.videos.clear()
        for base in (self.theater_root, self.product_root):
            if base is None:
                continue
            for path in (base / 'assets/video').glob('*.mp4'):
                if path.resolve().is_relative_to(base.resolve()) and is_video(path):
                    self.videos['assets/video/' + path.name] = self.register(path)
        for base in (self.card_root, self.product_root):
            if base is None:
                continue
            directory = base / IMAGE_BASE
            if directory.is_dir():
                for path in directory.glob('*.png'):
                    if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                        with path.open('rb') as stream:
                            if stream.read(8) == b'\x89PNG\r\n\x1a\n':
                                self.card_images[IMAGE_BASE + path.name] = self.register(path)
        for base in (self.banner_root, self.theater_root / 'assets' if self.theater_root else None, self.root):
            if base is None:
                continue
            directory = base / 'images' / 'album'
            if directory.is_dir():
                for path in sorted(directory.rglob('*')):
                    if path.is_file() and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'} and path.resolve().is_relative_to(base.resolve()):
                        logical_path = path.relative_to(base).as_posix()
                        self.banners[logical_path] = self.register(path)
                        with path.open('rb') as stream:
                            header = stream.read(24)
                        if header.startswith(b'\x89PNG\r\n\x1a\n') and len(header) == 24:
                            w, h = (int.from_bytes(header[16:20], 'big'), int.from_bytes(header[20:24], 'big'))
                            if 0 < w <= 20000 and 0 < h <= 20000:
                                self.banner_sizes[logical_path] = {'w': w, 'h': h}
        for base in (self.icon_root / 'assets' if self.icon_root else None, self.root):
            if base is None:
                continue
            for quality in ('', 'low', 'medium', 'high', 'ultra'):
                for variant in ('general', 'dmm'):
                    directory = base / quality / 'images' / 'character' / variant / 'i'
                    if not directory.is_dir():
                        continue
                    for path in directory.iterdir():
                        if not path.is_file() or path.suffix.lower() not in {'.bin', '.png'}:
                            continue
                        try:
                            if not path.resolve().is_relative_to(base.resolve()):
                                continue
                            with path.open('rb') as image:
                                if not is_icon_image(image.read(24)):
                                    continue
                            digest = path.stem
                            if re.fullmatch('icon_l_[0-9]{6}', digest):
                                digest = icon_hash(int(digest.rsplit('_', 1)[-1]))
                            if re.fullmatch('[a-f0-9]{32}', digest):
                                self.icons[digest] = {'url': self.register(path), 'source': 'game-original-icon'}
                        except (OSError, ValueError):
                            continue
        if not self.root.is_dir():
            self.issues.append('Game cache folder not found; text reading is still available.')
        scene_bases = [self.root] + ([self.scene_root / 'assets'] if self.scene_root else [])
        try:
            aliases, _ = load_character_resource_index(self.scene_root / 'character-index.json') if self.scene_root else ({}, set())
        except (OSError, ValueError) as error:
            aliases = {}
            self.issues.append(f'Could not load portrait skin mappings: {error}')
        for quality in ('', 'low', 'medium', 'high', 'ultra'):
            for base in scene_bases:
                folder = base / quality / 'images' / 'background'
                if folder.is_dir():
                    for path in folder.iterdir():
                        if path.is_file() and path.suffix.lower() in ('.jpg', '.png', '.webp') and path.resolve().is_relative_to(base.resolve()):
                            self.backgrounds[path.stem] = self.register(path)
        for category, folder in (('bgm', 'bgm'), ('se', 'sound'), ('voice', 'voice')):
            for base in (self.root, self.audio_root / 'assets' if self.audio_root else None):
                if base is None:
                    continue
                directory = base / folder
                if directory.is_dir():
                    for path in directory.rglob('*.mp3'):
                        if not path.is_file():
                            continue
                        relative = path.relative_to(directory).with_suffix('').as_posix()
                        self.audio[category][relative] = path
                        if category == 'se':
                            self.audio[category].setdefault(path.stem, path)
        metadata_files = [(base, path) for base in scene_bases for path in (base / 'json/bustup').rglob('*.json')]
        metadata_files.sort(key=lambda pair: (pair[1].parent.name != 'dmm', pair[0] == self.root, str(pair[1])))
        for base, metadata in metadata_files:
            try:
                if not metadata.resolve().is_relative_to(base.resolve()):
                    continue
                atlas = atlas_data(metadata.read_bytes()) if base != self.root else json.loads(decode_resource(metadata.read_bytes()))
                frames = atlas.get('frames', {})
                main = next((item for item in frames.values() if item.get('isSource')), None)
                if not main:
                    continue
                variant = metadata.parent.name
                image_path = None
                for quality in ('ultra', 'high', 'medium', 'low', ''):
                    for image_base in [base] + [other for other in scene_bases if other != base]:
                        candidate = image_base / quality / 'images' / 'character' / variant / 'bustup' / f'{metadata.stem}.bin'
                        if candidate.is_file() and candidate.resolve().is_relative_to(image_base.resolve()):
                            image_path = candidate
                            break
                    if image_path:
                        break
                if not image_path:
                    continue
                image = self.register(image_path)
                source_name = next((key.removesuffix('.png') for key, frame in frames.items() if frame.get('isSource')))
                bound_aliases = []
                if base != self.root:
                    for target_id, source_id in aliases.items():
                        prefix = 'bustup_' + source_id
                        if source_name == prefix or source_name.startswith(prefix + '_'):
                            requested_body = 'bustup_' + target_id + source_name[len(prefix):]
                            if metadata.stem == hashlib.md5((requested_body + '_all').encode('ascii')).hexdigest():
                                bound_aliases.append((target_id, prefix))
                head_reference = portrait_head_reference(frames)
                for name, item in frames.items():
                    descriptor = {'kind': 'atlas', 'url': image, 'size': main.get('sourceSize', {}), 'main': main, 'expression': None if item.get('isSource') else item, 'headReference': head_reference}
                    canonical = Path(name).stem
                    if not bound_aliases:
                        self.actors.setdefault(canonical, descriptor)
                    for target_id, prefix in bound_aliases:
                        if canonical == prefix or canonical.startswith(prefix + '_'):
                            self.actors.setdefault('bustup_' + target_id + canonical[len(prefix):], descriptor)
            except (OSError, ValueError, StopIteration) as error:
                self.issues.append(f'Could not read atlas metadata: {metadata.name} ({error})')
        for quality in ('', 'low', 'medium', 'high', 'ultra'):
            for base in scene_bases:
                directory = base / quality / 'images' / 'character'
                for pattern in ('bustup_*.png', 'cutin_*.png'):
                    for path in directory.rglob(pattern):
                        if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                            self.actors.setdefault(path.stem, {'kind': 'image', 'url': self.register(path)})
                if base != self.root:
                    for variant in ('dmm', 'general'):
                        for folder in LEGACY_FOLDERS:
                            for path in (directory / variant / folder).glob('*.png'):
                                if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                                    try:
                                        canonical = character_name(f'character/{folder}/{path.stem}')
                                        self.actors.setdefault(canonical, {'kind': 'image', 'url': self.register(path)})
                                    except ValueError:
                                        continue

    def resolve(self, kind: str, name: str):
        if kind == 'image':
            return self.card_images.get(name)
        if kind == 'character':
            try:
                canonical = character_name(name)
            except ValueError:
                return None
            if canonical in self.actors:
                return self.actors[canonical]
            digest = hashlib.md5(canonical.encode('ascii')).hexdigest()
            folder = character_folder(name)
            bases = [self.root] + ([self.scene_root / 'assets'] if self.scene_root else [])
            for quality in ('ultra', 'high', 'medium', 'low', ''):
                for variant in ('dmm', 'general'):
                    for base in bases:
                        for filename in (f'{digest}.bin', f'{canonical}.png'):
                            path = base / quality / 'images/character' / variant / folder / filename
                            if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                                with path.open('rb') as stream:
                                    header = stream.read(24)
                                if header.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
                                    self.actors[canonical] = {'kind': 'image', 'url': self.register(path)}
                                    return self.actors[canonical]
            return None
        if kind == 'background':
            return self.backgrounds.get(name) or self.backgrounds.get(BACKGROUND_ALIASES.get(name))
        if kind in self.audio:
            if not isinstance(name, str):
                return None
            exact = self._resolve_audio_exact(kind, name)
            if exact:
                return exact
            target = audio_target(kind, name)
            if target != (kind, name.removesuffix('.mp3')):
                fallback = self._resolve_audio_exact(*target)
                if fallback:
                    try:
                        validate_file(self.files[fallback.rsplit('/', 1)[-1]])
                    except (OSError, ValueError):
                        return None
                    return fallback
            return None
        return None

    def _resolve_audio_exact(self, kind, name):
        group = self.audio[kind]
        try:
            remote = audio_path(kind, name)
            if kind == 'voice':
                relative = remote.removeprefix('assets/voice/').removesuffix('.mp3')
                logical = ('f/' if '_forever' in name else 'c/') + name.removesuffix('.mp3')
                keys = (relative, logical)
            elif kind == 'bgm':
                keys = (name.removesuffix('.mp3'),)
            else:
                filename = name.rsplit('/', 1)[-1].removesuffix('.mp3')
                digest = hashlib.md5(filename.encode()).hexdigest()
                parent = name.rpartition('/')[0]
                keys = (name.removesuffix('.mp3'), f'{parent}/{digest}', digest)
        except ValueError:
            return None
        roots = [self.root] + ([self.audio_root / 'assets'] if self.audio_root else [])
        path = next((group[key] for key in keys if key in group), None)
        if path is None:
            relative_path = remote.removeprefix('assets/')
            path = next((root / relative_path for root in reversed(roots) if (root / relative_path).is_file()), None)
        if path is not None and path.is_file() and any((path.resolve().is_relative_to(root.resolve()) for root in roots)):
            return self.register(path)
        return None

    def story_assets(self, references: dict) -> dict:
        resolved, missing = ({}, [])
        for kind, names in references.items():
            resolved[kind] = {}
            for name in names:
                asset = self.resolve(kind, name)
                if asset:
                    resolved[kind][name] = asset
                else:
                    missing.append({'kind': kind, 'name': name})
        return {'resolved': resolved, 'missing': missing}

    def category_banner(self, category):
        filename = CATEGORY_BANNER_FILES.get(category)
        path = f'images/album/banner/{filename}' if filename else None
        url = self.banners.get(path)
        return {'url': url, 'path': path, 'size': self.banner_sizes.get(path), 'source': 'game-category', 'level': 'category'} if url else None

    def character_icon(self, character_id):
        try:
            icon = self.icons.get(icon_hash(character_id))
            return icon | {'characterId': character_id} if icon else None
        except ValueError:
            return None

    def character_thumbnail(self, character_id):
        if type(character_id) is not int or not 100000 <= character_id <= 999999999:
            return None
        prefix = f'bustup_{character_id}'
        for name in (prefix + '_01', prefix):
            if name in self.actors:
                return self.actors[name]
        name = next((key for key in sorted(self.actors) if key.startswith(prefix + '_')), None)
        return self.actors.get(name)

    def group_banner(self, item):
        path = group_banner_path(item)
        if not path:
            return None
        url = self.banners.get(path)
        if not url:
            legacy = normalize_banner_path(item.get('bannerPath'))
            if legacy in self.banners:
                path, url = (legacy, self.banners[legacy])
        return {'url': url, 'path': path, 'size': self.banner_sizes.get(path), 'source': 'masterAlbumDungeon.dungeonBunnerUrl', 'level': 'group'} if url else None

    def main_part_banner(self, item):
        for variant in ('dmm', 'general'):
            path = main_part_banner_path(item, variant)
            url = self.banners.get(path)
            if url:
                return {'url': url, 'path': path, 'size': self.banner_sizes.get(path), 'source': 'game-main-part', 'level': 'part'}
        return None

    @staticmethod
    def content_type(path: Path):
        if path.suffix == '.bin':
            with path.open('rb') as source:
                magic = source.read(12)
            if magic.startswith(b'\x89PNG'):
                return 'image/png'
            if magic.startswith(b'\xff\xd8'):
                return 'image/jpeg'
            if magic.startswith(b'RIFF'):
                return 'image/webp'
        return mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
