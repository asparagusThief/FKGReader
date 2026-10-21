"""Exact compatibility aliases loaded from local content."""
from .runtime_data import title_table, audio_aliases
BACKGROUND_ALIASES = title_table('backgroundAliases')
AUDIO_ALIASES = audio_aliases()

def audio_target(kind, reference):
    name = reference.removesuffix('.mp3') if isinstance(reference, str) else reference
    return AUDIO_ALIASES.get((kind, name), (kind, name))

def audio_candidates(kind, reference):
    original = (kind, reference)
    target = audio_target(kind, reference)
    return [original] if target == (kind, reference.removesuffix('.mp3')) else [original, target]
