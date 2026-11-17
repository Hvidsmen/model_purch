"""Apply the agreed HAProxy/TT profile on the portal server."""
from importlib.util import find_spec
from pathlib import Path
import shutil
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from portal.deployment import da_dev_profile,site_settings
from base.ldaps import check_tls
from scripts.split_monitoring_runtime import read_config,lock,stop
from scripts.split_monitoring_update import write


class Command(BaseCommand):
    help='Настроить da-dev.daichi.ru за HAProxy и LDAPS домена TT; проверить TLS без пароля.'

    def add_arguments(self,parser):
        parser.add_argument('--ca-cert',help='PEM-файл публичной цепочки CA, если её нет в доверенных сертификатах Windows.')

    def handle(self,*args,**options):
        if find_spec('ldap3') is None:raise CommandError('Установите ldap3: python -m pip install "ldap3>=2.9.1,<3"')
        ca=options.get('ca_cert')
        if ca is None:ca=getattr(settings,'AD_CA_CERT_FILE','')
        if ca:
            path=Path(ca).expanduser().resolve()
            if not path.is_file():raise CommandError('Не найден PEM-файл CA.')
            ca=str(path)
        profile=da_dev_profile(ca)
        try:protocol=check_tls(profile['PORTAL_AD_LDAPS_URL'],ca,settings.AD_CONNECT_TIMEOUT)
        except (OSError,ValueError) as exc:
            raise CommandError('LDAPS TLS не проверен ('+type(exc).__name__+'). Настройки не изменены. Проверьте DNS, порт 636 и доверие к цепочке CA; при необходимости укажите --ca-cert. Проверка сертификата остаётся обязательной.') from None
        destination=Path(settings.BASE_DIR)/'.local/site-settings.json'
        current=site_settings(settings.BASE_DIR)
        # Read first, then serialize the monitor restart with its Git updater.
        monitor_path=Path(settings.SPLIT_MONITORING_CONFIG)
        if monitor_path.exists():
            with lock(monitor_path.resolve().parent/'activation.lock'),lock(monitor_path.resolve().parent/'refresh.lock'):
                config=read_config(monitor_path)
                origin='https://da-dev.daichi.ru'
                if origin not in config['origins']:
                    stop(monitor_path)
                    config['origins']=[*config['origins'],origin]
                    write(monitor_path,config)
        if destination.exists():shutil.copy2(destination,destination.with_suffix('.json.bak'))
        write(destination,{**current,**profile})
        self.stdout.write(self.style.SUCCESS('LDAPS: '+protocol+'. Сертификат и имя контроллера проверены; пароль не отправлялся.'))
        self.stdout.write('Сохранён профиль https://da-dev.daichi.ru; доверенный прокси 192.168.221.9; LDAPS TT и короткие логины. Права пользователей не изменены.')
        self.stdout.write('Перезапустите портал. Мониторинг получит новый origin при следующем открытии. Проверка TLS не заменяет проверку входа реального пользователя.')
