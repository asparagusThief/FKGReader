"""Local resource parsing and validation; no network fetching."""
from __future__ import annotations

def validate_date_master(payload):
    if not isinstance(payload, dict) or payload.get('kind') != 'fkg-date-master':
        raise ValueError('Not an FKG date master export')
    if payload.get('schemaVersion') not in (1, 2):
        raise ValueError('Unsupported date export schema')
    for name in ('masterCharacterBook', 'masterDateCharacters'):
        rows = payload.get(name)
        if not isinstance(rows, list) or not rows or (not all((isinstance(row, dict) for row in rows))):
            raise ValueError(f'Missing or invalid {name}')
    return payload
