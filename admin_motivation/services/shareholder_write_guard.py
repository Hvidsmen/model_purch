"""Check write targets after persistent calculation outputs become private temps."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

IDENTIFIER = r'(?:\[(?:[^\]]|\]\])+\]|"(?:[^"]|"")+"|[#@\w]+)'
TABLE = IDENTIFIER + r'(?:\s*\.\s*' + IDENTIFIER + r')*'
WRITES = re.compile(r'\b(?P<operation>INSERT(?:\s+INTO)?|INTO|UPDATE|DELETE(?:\s+FROM)?|DROP\s+TABLE|CREATE\s+TABLE|ALTER\s+TABLE|TRUNCATE\s+TABLE)\s+', re.I)
TARGET = re.compile(TABLE)
REFERENCES = re.compile(r'\b(?:FROM|JOIN)\s+(?P<table>'+TABLE+r')\s+(?:AS\s+)?(?P<alias>'+IDENTIFIER+r')', re.I)


def identifier(value):
    value=value.strip()
    if value.startswith('[') and value.endswith(']'):return value[1:-1].replace(']]',']')
    if value.startswith('"') and value.endswith('"'):return value[1:-1].replace('""','"')
    return value


def local_table(value):
    # Global ## tables may belong to other sessions and are not private outputs.
    return bool(re.fullmatch(r'#[\w]+',identifier(value)))


def local_alias(statement,target):
    """Resolve FROM/JOIN at this statement's outer level, not a nested SELECT."""
    target=identifier(target).casefold()
    depth=0;position=0;resolved=[]
    for reference in REFERENCES.finditer(statement):
        for char in statement[position:reference.start()]:
            if char=='(':depth+=1
            elif char==')':depth-=1
        if depth==0 and identifier(reference['alias']).casefold()==target:
            resolved.append(local_table(reference['table']))
        position=reference.end()
    return bool(resolved) and all(resolved)


def validate_private_writes(source):
    code=mask_sql(source)
    for write in WRITES.finditer(code):
        match=TARGET.match(code,write.end())
        target=match[0] if match else ''
        if match and local_table(target):continue
        operation=write['operation'].upper()
        end=code.find(';',write.end())
        statement=code[match.end() if match else write.end():end if end>=0 else len(code)]
        if match and operation in ('UPDATE','DELETE','DELETE FROM') and re.fullmatch(IDENTIFIER,target) and local_alias(statement,target):continue
        line=source.count('\n',0,write.start())+1
        raise ValidationError('Установленная процедура содержит запись вне временных таблиц. '
            f'Операция: {operation}; цель: {target or "не распознана"}; строка пакета: {line}. Нужна проверка совместимости.')
