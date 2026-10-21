"""Search-only Unicode/kana folding; original labels and dialogue stay intact."""
import unicodedata
_KANA_FOLD = {code: code - 96 for code in (*range(12449, 12535), 12541, 12542)}
_KANA_FOLD.update({ord(kata): hira + '゙' for kata, hira in zip('ヷヸヹヺ', 'わゐゑを')})

def normalize_search(value: str) -> str:
    text = unicodedata.normalize('NFKC', value).translate(_KANA_FOLD)
    return unicodedata.normalize('NFC', text).casefold().strip()
