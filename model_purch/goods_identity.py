"""Stable, Unicode-aware planning-group identity shared by SQLite and SQL Server."""
import hashlib
import unicodedata


def planning_group_key(value):
    normalized = unicodedata.normalize('NFC', str(value or '').strip()).casefold()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def source_goods_key(code):
    """Separate ERP identities from hashes of planning-group names."""
    return 'erp:'+planning_group_key(code)[:60]
