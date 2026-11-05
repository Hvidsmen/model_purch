"""HEAT classification, accepting the numbered labels used by the ERP."""
import re


def is_heat(value):
    return re.sub(r'^[0-9. ]+', '', str(value or '').strip().upper()) == 'HEAT'


def predicate(expression):
    value = f"UPPER(LTRIM(RTRIM(COALESCE({expression},N''))))"
    return f"COALESCE(SUBSTRING({value},NULLIF(PATINDEX(N'%[^0-9. ]%',{value}),0),255),N'')=N'HEAT'"
