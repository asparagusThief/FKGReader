"""Loopback-only local server. Python standard library; no game login required."""
from __future__ import annotations
import argparse
import json
import re
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from core.assets import AssetCatalog
from core.library import StoryLibrary, parse_story_identity
from core.banners import group_banner_path, main_part_banner_path
from core.translations import TranslationCatalog
from core.personal_translations import PersonalTranslations, EditConflict
from core.runtime_paths import RuntimePaths
ROOT = Path(__file__).resolve().parent

class App:

    def __init__(self):
        p = self.paths = RuntimePaths(ROOT)
        self.config = p.config
        self.library = StoryLibrary(p.story_root, p.catalog_root, p.date_root, card_asset_root=p.card_root)
        self.translations = TranslationCatalog(p.translation_root, p.glossary_root, p.additional_glossary_roots)
        self.personal_translations = PersonalTranslations(p.user_root / 'personal-translations.json')
        self.assets = AssetCatalog(p.cache_root, p.banner_root, p.icon_root, p.card_root, p.scene_root, p.audio_root, p.theater_root)

    def decorate_card(self, card):
        return card | {'banner': self.assets.group_banner(card), 'categoryBanner': self.assets.category_banner(card['categoryKey']), 'characterIcon': self.assets.character_icon(card.get('iconCharacterId')), 'participants': [person | {'icon': self.assets.character_icon(person['characterId'])} for person in card.get('participants', [])]}

    def category_options(self):
        return [option | {'banner': self.assets.category_banner(option['key'])} for option in self.library.category_options()]

    def banner_summary(self):
        groups = self.library.groups()
        refs = [group for group in groups if group_banner_path(group)]
        parts = {group['partKey']: group for group in groups if main_part_banner_path(group)}
        return {'categories': sum((bool(option['banner']) for option in self.category_options())), 'groups': sum((bool(self.assets.group_banner(group)) for group in refs)), 'referencedGroups': len(refs), 'missingGroups': sum((not self.assets.group_banner(group) for group in refs)), 'parts': sum((bool(self.assets.main_part_banner(part)) for part in parts.values())), 'referencedParts': len(parts), 'missingParts': sum((not self.assets.main_part_banner(part) for part in parts.values()))}

class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, format, *args):
        pass

    def send_json(self, value, status=200, download=None):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, content_type):
        size = path.stat().st_size
        first, last, status = (0, max(0, size - 1), 200)
        range_header = self.headers.get('Range', '')
        match = re.fullmatch('bytes=(\\d+)-(\\d*)', range_header)
        if match:
            first = int(match[1])
            last = min(int(match[2]) if match[2] else size - 1, size - 1)
            if first >= size or last < first:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if content_type.startswith('text/'):
            self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(last - first + 1 if size else 0))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if status == 206:
            self.send_header('Content-Range', f'bytes {first}-{last}/{size}')
        self.end_headers()
        with path.open('rb') as source:
            source.seek(first)
            remaining = last - first + 1
            while remaining > 0:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/api/status':
                self.send_json({'app': 'fkg-reader', 'config': self.app.config, 'instanceId': self.app.paths.instance_id, 'portable': self.app.paths.portable, 'stories': len(self.app.library.cards), 'categories': [option['label'] for option in self.app.library.category_options()], 'categoryOptions': self.app.category_options(), 'banners': self.app.banner_summary(), 'catalogOrder': 'game-album', 'metadata': self.app.library.metadata_summary(), 'dateLibrary': self.app.library.date_summary, 'messageCards': self.app.library.messagecard_summary | {'cachedImages': len(self.app.assets.card_images)}, 'theater': self.app.library.theater_summary | {'cachedVideos': sum((entry.get('theaterMedia', {}).get('assetPath') in self.app.assets.videos for entry in self.app.library.entries.values()))}, 'sceneAssets': {'root': str(self.app.assets.scene_root), 'downloading': (self.app.assets.scene_root / 'download.lock').is_file()}, 'assets': {'expressions': len(self.app.assets.actors), 'backgrounds': len(self.app.assets.backgrounds), 'bgm': len(self.app.assets.audio['bgm']), 'characterIcons': len(self.app.assets.icons)}, 'issues': self.app.assets.issues + self.app.library.issues})
            elif url.path == '/api/translation-terms':
                self.send_json({'items': self.app.translations.terms(self.app.library.icon_books, self.app.assets.character_icon, lambda row: self.app.library.speaker_icons.portrait_for_term(row, self.app.assets.character_thumbnail))})
            elif url.path == '/api/personal-translations':
                if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                    self.send_json({'error': 'A loopback host is required'}, 403)
                    return
                self.send_json(self.app.personal_translations.export(), download='fkg-personal-translations.json')
            elif url.path == '/api/groups':
                self.send_json({'items': [group | {'banner': self.app.assets.group_banner(group), 'partBanner': self.app.assets.main_part_banner(group), 'characterIcon': self.app.assets.character_icon(group.get('iconCharacterId'))} for group in self.app.library.groups(query.get('category', [''])[0])]})
            elif url.path == '/api/stories':
                result = self.app.library.listing(query.get('q', [''])[0], query.get('category', [''])[0], max(0, int(query.get('offset', [0])[0])), min(200, max(1, int(query.get('limit', [80])[0]))), {parse_story_identity(value) for value in query['ids'][0].split(',') if value} if 'ids' in query else None, group=query.get('group', [''])[0], part=query.get('part', [''])[0])
                self.send_json(result | {'items': [self.app.decorate_card(card) for card in result['items']]})
            elif re.fullmatch('/api/story/(?:[0-9]+|date:[0-9]{6}:100|message:[1-9][0-9]{0,9}|theater:[1-9][0-9]{0,9})', url.path):
                payload = self.app.library.read(parse_story_identity(url.path.rsplit('/', 1)[1]))
                if payload['story']['categoryKey'] == 'unknown':
                    self.send_json({'error': 'This story is not in the reader catalog'}, 404)
                    return
                if 'theaterMedia' in payload:
                    media = payload['theaterMedia']
                    payload = payload | {'theaterMedia': media | {'url': self.app.assets.videos.get(media.get('assetPath'))}}
                self.send_json(payload | {'story': self.app.decorate_card(payload['story']), 'speakerIcons': self.app.library.speaker_icons.for_story(payload, self.app.assets.character_icon), 'portraitIcons': self.app.library.speaker_icons.for_portraits(payload, self.app.assets.character_icon), 'translation': self.app.translations.for_story(payload), 'personalTranslations': self.app.personal_translations.for_story(payload), 'assets': self.app.assets.story_assets(payload['script']['references'])})
            elif re.fullmatch('/api/asset/[a-f0-9]{24}', url.path):
                path = self.app.assets.files[url.path.rsplit('/', 1)[1]]
                self.send_file(path, self.app.assets.content_type(path))
            else:
                static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/engine.js': 'engine.js', '/ui-language.js': 'ui-language.js', '/styles.css': 'styles.css'}
                filename = static.get(url.path)
                if not filename:
                    self.send_json({'error': 'Resource not found'}, 404)
                    return
                path = ROOT / 'web' / filename
                content_type = 'text/html; charset=utf-8' if filename.endswith('.html') else 'text/css; charset=utf-8' if filename.endswith('.css') else 'text/javascript; charset=utf-8'
                self.send_file(path, content_type)
        except (KeyError, FileNotFoundError):
            self.send_json({'error': 'Story or asset not found'}, 404)
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as error:
            traceback.print_exc()
            self.send_json({'error': str(error)}, 500)

    def do_POST(self):
        try:
            if self.path.startswith('/api/personal-translations') and self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                self.send_json({'error': 'A loopback host is required'}, 403)
                return
            origin = self.headers.get('Origin')
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send_json({'error': 'Only requests from this local reader are accepted'}, 403)
                return
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                self.send_json({'error': 'A JSON request is required'}, 415)
                return
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 20 * 1024 * 1024:
                self.send_json({'error': 'File too large'}, 413)
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('A JSON object is required')
            if self.path == '/api/import-metadata':
                if self.app.paths.portable:
                    self.send_json({'error': 'Local content is read-only. Stop the reader before replacing compatible data.'}, 403)
                else:
                    self.send_json(self.app.library.import_metadata(payload))
            elif self.path == '/api/personal-translations/save':
                story = self.app.library.read(parse_story_identity(str(payload.get('storyId', ''))))
                self.send_json(self.app.personal_translations.save(payload, story))
            elif self.path == '/api/personal-translations/import':
                self.send_json(self.app.personal_translations.import_document(payload.get('document'), replace=payload.get('replace') is True, preview=payload.get('preview') is True))
            elif self.path == '/api/refresh':
                self.app.library.refresh()
                self.app.assets.refresh()
                self.send_json({'stories': len(self.app.library.cards)})
            elif self.path == '/api/shutdown':
                self.send_json({'stopping': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_json({'error': 'Operation not found'}, 404)
        except EditConflict as error:
            self.send_json({'error': str(error)}, 409)
        except (KeyError, FileNotFoundError):
            self.send_json({'error': 'Story not found'}, 404)
        except (ValueError, TypeError, OSError) as error:
            self.send_json({'error': str(error)}, 400)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    try:
        Handler.app = App()
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except (OSError, ValueError) as error:
        print(f'Startup error: {error}', file=sys.stderr)
        return 1
    print(f'FKG Reader: http://127.0.0.1:{args.port} ({len(Handler.app.library.cards)} stories)', flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
