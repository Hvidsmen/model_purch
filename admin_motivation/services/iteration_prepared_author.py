"""Single prepared deal-author source for all iteration periods and allocations."""
from .prepared_sales import TABLE
FIELD='Даичи_АвторСделкиНаименование'
LABEL=TABLE+'.'+FIELD


def normalize_private(table):
    # Only normalize a private calculation copy. Never substitute another
    # author field or re-enrich against a different publication.
    return f"UPDATE {table} SET [{FIELD}]=NULLIF(LTRIM(RTRIM([{FIELD}])),N'');\n"
