# Портал планирования

Python 3.10–3.12, Django 5.2. Приложения: `PlanningSystem` (планы продаж и сезонность),
`model_purch` (сценарии закупок и расчёты), `admin_motivation` (коэффициенты мотивации),
`RefEditor` (справочники). Интерфейс использует Bootstrap/CoreUI и Plotly.

## Запуск на Windows (PowerShell)

Из корня репозитория. Если `.venv` уже существует, проверьте
`.\.venv\Scripts\python.exe --version`: поддерживаются версии 3.10–3.12.
Если версия вне этого диапазона, переименуйте `.venv` в резервную папку и создайте среду через
`py -3.10 -m venv .venv`. Для установленного Python 3.10 обновление до 3.12 не требуется.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force .local | Out-Null
.\.venv\Scripts\python.exe manage.py migrate --settings=portal.settings_local
.\.venv\Scripts\python.exe manage.py check --settings=portal.settings_local
.\.venv\Scripts\python.exe manage.py test --settings=portal.settings_local --noinput
.\.venv\Scripts\python.exe manage.py runserver --settings=portal.settings_local
```

## Linux / облачная среда

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p .local
.venv/bin/python manage.py migrate --settings=portal.settings_local
.venv/bin/python manage.py check --settings=portal.settings_local
.venv/bin/python manage.py test --settings=portal.settings_local --noinput
.venv/bin/python manage.py runserver 127.0.0.1:8000 --settings=portal.settings_local
```

Локальные настройки создают отдельную БД `.local/db.sqlite3` и сохраняют загрузки в
`.local/media`. Исходная `db.sqlite3` и бизнес-файлы Excel сохранены. Для работы с
копией исходных данных можно задать `DJANGO_DB_PATH` на файл копии и выполнить миграции.
Для пустой БД создайте администратора через `manage.py createsuperuser` и внесите
справочники через `/admin/`. Примеры шаблонов загрузки (`TemplatesFile` с ID 1 для
филиалов и ID 2 для сезонности) необязательны для открытия страниц.

Основные страницы: `/`, `/model_purch/`, `/plannging_system/`, `/ref_editor/ref-store-group/`.
Написание `plannging_system` сохранено для совместимости существующих адресов.
Проверки выполняются с отдельной временной тестовой БД, без корпоративных записей.

## SQL Server и расчёты

Локальный режим позволяет разрабатывать интерфейс и выполнять тесты без SQL Server.
Синхронизация, расчёт заказов, графики корпоративных остатков и импорт данных в DWH
требуют реального доступа к серверу и схемам. Эти операции изменяют корпоративные
таблицы; тесты заменяют подключения к SQL Server и не выполняют их на реальном сервере.

Настройте переменные процесса (не сохраняйте пароли в Git):

- `MS_SQL_CONN_STR`: ODBC-подключение к базе `ModelPurch`.
- `DWH_SQL_CONN_STR`: ODBC-подключение к базе `DataWH`.
- `DJANGO_DB_PATH`: необязательный путь к SQLite.
- `DJANGO_SECRET_KEY`: ключ Django; обязателен при `DJANGO_DEBUG=false`.
- `DJANGO_ALLOWED_HOSTS`: разрешённые имена серверов через запятую.

На машине нужен ODBC-драйвер, указанный в строке подключения. Старое подключение
`SQL Server` с `Trusted_Connection=yes` зависит от Windows-аутентификации; для Linux
нужны подходящий драйвер и поддерживаемая сервером схема аутентификации.
SQL-процедуры, представления и SQL Agent job `ModelPurch` в этом репозитории отсутствуют.
Полный расчёт нельзя проверить только по Python-коду без этих внешних компонентов.
Последний шаг запускает задание пересчёта куба, но не подтверждает его завершение.

`portal.settings` сохраняет старое Windows-подключение как значение по умолчанию,
если переменные не заданы. Для разработки без корпоративной сети используйте
`portal.settings_local`. Старые Dash-модули отключены по умолчанию: действующая страница
графиков использует JSON API и Plotly. Их отдельная интеграция требует настройки
`django-plotly-dash`, его маршрутов и приложений; один флаг не заменяет эту настройку.

## Git

`.gitignore` исключает Python-кэш, виртуальные среды, локальные БД и `.env`.
Существующие бизнес-файлы и исходная БД сохранены, новые рабочие данные храните в `.local`.
После получения изменений: `git pull`, установка `requirements.txt`, миграции и тесты.
Код совместимости `model_purch.views__` обращается к действующим обработчикам
`model_purch.views`; новые обработчики подключайте через `model_purch/urls.py`.

## Зависимости на Windows

Основной `requirements.txt` содержит прямые зависимости приложения, совместимые с
Python 3.10–3.12, и NumPy 2.2.6. Транзитивные зависимости выбирает pip для вашей платформы;
снимок всех пакетов облачной машины не используется как список требований Windows.
Разрешение основного списка проверено для `win_amd64` / Python 3.10 и 3.12 с готовыми wheels.
Для необязательной интеграции старого Dash используйте `requirements-dash.txt`;
обычный интерфейс, расчёты и тесты этого дополнения не требуют.
