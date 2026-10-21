"""Program files, compatible local content and personal files stay separate."""
import hashlib
import json
from pathlib import Path

CONTENT_FORMAT = 1


class RuntimePaths:
    def __init__(self, app_root):
        self.app_root = Path(app_root).resolve()
        self.install_root = self.app_root.parent
        self.portable = True
        self.content_root = self.install_root / "content"
        self.user_root = self.install_root / "userdata"
        for root in (self.content_root, self.user_root):
            if not root.resolve().is_relative_to(self.install_root):
                raise ValueError("Content/userdata must stay inside this reader folder")
        manifest_path = self.content_root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("Local content is missing. See README.md: place compatible data in content/.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "fkg-reader-content" or type(manifest.get("formatVersion")) is not int or manifest["formatVersion"] != CONTENT_FORMAT:
            raise ValueError("Unsupported local content format")
        if (self.install_root / "BUILDING.json").exists():
            raise ValueError("Program setup is incomplete")
        self.story_root = self.content_root / "stories"
        self.date_root = self.content_root / "dates"
        self.catalog_root = self.content_root / "catalog"
        self.translation_root = self.content_root / "translations"
        self.glossary_root = self.content_root / "glossary"
        self.additional_glossary_roots = []
        for filename in ("display-titles.json", "script-compat-profiles.json"):
            if not (self.catalog_root / filename).is_file():
                raise FileNotFoundError("Local content is incomplete: content/catalog/" + filename)
        assets = self.content_root / "assets"
        for name, folder in (("cache_root", "cache"), ("banner_root", "banners"), ("icon_root", "icons"),
                             ("card_root", "messagecards"), ("scene_root", "scenes"),
                             ("audio_root", "audio"), ("theater_root", "theater")):
            setattr(self, name, assets / folder)
        self.config = {"layoutVersion": 1, "storyRoot": "content/stories",
                       "dateRoot": "content/dates", "cacheRoot": "content/assets/cache"}

    @property
    def instance_id(self):
        return hashlib.sha256(str(self.app_root).encode("utf-8")).hexdigest()[:24]
