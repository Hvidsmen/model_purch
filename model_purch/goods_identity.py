"""Stable, Unicode-aware planning-group identity shared by SQLite and SQL Server."""
import hashlib
import unicodedata


def planning_group_key(value):
    normalized = unicodedata.normalize('NFC', str(value or '').strip()).casefold()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
