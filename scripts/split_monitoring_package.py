"""Validate a private deployment ZIP before any application code is executed."""
from hashlib import sha256
import json
from pathlib import Path,PurePosixPath
import re
from zipfile import ZipFile,BadZipFile

MAX_UPLOAD_BYTES=64*1024*1024
MAX_EXPANDED_BYTES=200*1024*1024


class PackageError(ValueError):pass


def safe_name(name):
    path=PurePosixPath(name)
    if not name or path.is_absolute() or '\\' in name or ':' in name or any(part in ('','.','..') for part in name.split('/')):
        raise PackageError('В ZIP обнаружен небезопасный путь.')
    for part in path.parts:
        if part.rstrip(' .')!=part or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):
            raise PackageError('В ZIP обнаружено неподдерживаемое имя файла.')
    return path


def extract_verified(package,destination):
    destination=Path(destination)
    try:
        with ZipFile(package) as archive:
            items=archive.infolist()
            if len(items)>10000 or sum(item.file_size for item in items)>MAX_EXPANDED_BYTES:
                raise PackageError('Распакованный пакет превышает лимит 200 МБ или число файлов.')
            names=set()
            for item in items:
                safe_name(item.filename.rstrip('/') if item.is_dir() else item.filename)
                name=item.filename.rstrip('/').casefold()
                if name in names:raise PackageError('В ZIP есть повторяющиеся имена файлов.')
                names.add(name)
                mode=(item.external_attr>>16)&0o170000
                if mode not in (0,0o100000,0o040000):raise PackageError('Ссылки и специальные файлы в ZIP запрещены.')
                if item.flag_bits&1:raise PackageError('ZIP с паролем не поддерживается.')
            archive.extractall(destination)
        manifests=list(destination.glob('PACKAGE_MANIFEST.json'))+list(destination.glob('*/PACKAGE_MANIFEST.json'))
        if len(manifests)!=1:raise PackageError('Нужен полный приватный ZIP с одним PACKAGE_MANIFEST.json.')
        root=manifests[0].parent
        manifest=json.loads(manifests[0].read_text(encoding='utf-8-sig'))
        if manifest.get('packageKind')!='private-portal-deployment':raise PackageError('Это не полный приватный пакет размещения.')
        listed=set()
        for item in manifest['files']:
            name=str(safe_name(item['path']))
            if name.casefold() in listed:raise PackageError('В манифесте повторяется файл.')
            listed.add(name.casefold());path=root/name
            if not re.fullmatch('[0-9a-f]{64}',item['sha256']) or not path.is_file() or sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise PackageError('Контрольная сумма одного из файлов пакета не совпала.')
        actual={p.relative_to(root).as_posix().casefold() for p in root.rglob('*') if p.is_file() and p!=manifests[0]}
        if actual!=listed:raise PackageError('Состав ZIP не совпал с манифестом.')
        for name in ('portal_wsgi.py','requirements-portal.txt','app/dist/index.html','app/dist/data-app-build.json',
                     'data/template.json','data/planning_snapshot.json','data/reviewed_snapshot.json'):
            if not (root/name).is_file():raise PackageError('В полном пакете отсутствует '+name+'.')
        return root,manifest
    except PackageError:raise
    except (BadZipFile,OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        raise PackageError('Не удалось проверить ZIP: архив или манифест повреждён.') from exc
