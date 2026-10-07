# RefEditor/services.py
import logging
import pandas as pd
from django.db import transaction

from .models import StoreGroupOZP, Subdivision, GroupOZP, SubGroupOZP

logger = logging.getLogger(__name__)


class StoreGroupImportError(Exception):
    """Ошибка при импорте справочника."""
    pass


class StoreGroupImportService:
    """Сервис импорта складов из Excel."""

    SHEET_NAME = 'Загрузка'
    REQUIRED_COLUMNS = 4

    @classmethod
    @transaction.atomic
    def import_from_excel(cls, file) -> int:
        """
        Импортирует данные из Excel-файла.
        Возвращает количество обработанных строк.
        """
        try:
            df = pd.read_excel(file, sheet_name=cls.SHEET_NAME).fillna('')
        except ValueError as e:
            raise StoreGroupImportError(
                f'Лист "{cls.SHEET_NAME}" не найден в файле'
            ) from e
        except Exception as e:
            raise StoreGroupImportError(f'Ошибка чтения файла: {e}') from e

        if df.shape[1] < cls.REQUIRED_COLUMNS:
            raise StoreGroupImportError(
                f'Ожидается минимум {cls.REQUIRED_COLUMNS} столбца, '
                f'получено {df.shape[1]}'
            )

        processed = 0
        for row in df.itertuples(index=False):
            sub_raw, store_raw, g_raw, sg_raw = row[:4]

            sub = cls._clean(sub_raw, 'Подразделение')
            store = cls._clean(store_raw, 'Склад')
            g = cls._clean(g_raw, 'Группа')
            sg = cls._clean(sg_raw, 'Подгруппа')

            sub_obj = Subdivision.get_or_create(sub)
            g_obj = GroupOZP.get_or_create(g)
            sg_obj = SubGroupOZP.get_or_create(sg)

            key = StoreGroupOZP.make_key(store, sub)

            # Удаляем старые записи с таким же ключом и создаём новую
            StoreGroupOZP.objects.filter(key_store_sub=key).delete()
            StoreGroupOZP.objects.create(
                key_store_sub=key,
                store_name=store,
                subdivision=sub_obj,
                group_ozp=g_obj,
                sub_group_ozp=sg_obj,
            )
            processed += 1

        logger.info('Импортировано записей: %d', processed)
        return processed

    @staticmethod
    def _clean(value: str, field_name: str) -> str:
        value = str(value).strip()
        if not value:
            raise StoreGroupImportError(f'{field_name} не может быть пустым')
        return value


class ReferenceUpsertService:
    """Сервис для upsert-операций над справочниками."""

    @staticmethod
    @transaction.atomic
    def upsert_group_ozp(name: str) -> GroupOZP:
        name = name.strip()
        if not name:
            raise ValueError('Название группы не может быть пустым')
        group, _ = GroupOZP.objects.get_or_create(group_ozp_name=name)
        return group

    @staticmethod
    @transaction.atomic
    def upsert_sub_group_ozp(name: str) -> SubGroupOZP:
        name = name.strip()
        if not name:
            raise ValueError('Название подгруппы не может быть пустым')
        group, _ = SubGroupOZP.objects.get_or_create(sub_group_ozp_name=name)
        return group

    @staticmethod
    @transaction.atomic
    def upsert_store(store_name: str, subdivision_id: int,
                     group_ozp_id: int, sub_group_ozp_id: int) -> StoreGroupOZP:
        subdivision = Subdivision.objects.get(id=subdivision_id)
        group_ozp = GroupOZP.objects.get(id=group_ozp_id)
        sub_group_ozp = SubGroupOZP.objects.get(id=sub_group_ozp_id)

        key = StoreGroupOZP.make_key(store_name, subdivision.subdivision_name)
        StoreGroupOZP.objects.filter(key_store_sub=key).delete()

        return StoreGroupOZP.objects.create(
            key_store_sub=key,
            store_name=store_name,
            subdivision=subdivision,
            group_ozp=group_ozp,
            sub_group_ozp=sub_group_ozp,
        )