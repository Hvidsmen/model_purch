"""Temporary mode. Rebuild/rerun the calculation after changing this switch."""
import json
import os
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def enabled():
    override=getattr(settings,'MOTIVATION_MAIN_ALL_QUARTERS',None)
    if override is not None:return bool(override)
    config=Path(settings.BASE_DIR)/'.local'/'motivation-main-periods.json'
    if config.is_file():
        try:
            value=json.loads(config.read_text(encoding='utf-8-sig')).get('all_quarters',True)
            if not isinstance(value,bool):raise ValueError
            return value
        except (ValueError,KeyError,OSError) as error:
            raise ImproperlyConfigured('motivation-main-periods.json: all_quarters должен быть true или false.') from error
    return os.environ.get('MOTIVATION_MAIN_ALL_QUARTERS','1')=='1'


def periods():return tuple('12345') if enabled() else ('4','5')


def all_subdivisions():
    """Independent rollback for the temporary all-subdivision calculation."""
    override=getattr(settings,'MOTIVATION_MAIN_ALL_SUBDIVISIONS',None)
    if override is not None:return bool(override)
    config=Path(settings.BASE_DIR)/'.local'/'motivation-main-periods.json'
    if config.is_file():
        try:
            value=json.loads(config.read_text(encoding='utf-8-sig')).get('all_subdivisions',True)
            if not isinstance(value,bool):raise ValueError
            return value
        except (ValueError,OSError) as error:
            raise ImproperlyConfigured('motivation-main-periods.json: all_subdivisions должен быть true или false.') from error
    return os.environ.get('MOTIVATION_MAIN_ALL_SUBDIVISIONS','1')=='1'
