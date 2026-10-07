# model_purch/management/commands/import_purch.py
from django.core.management.base import BaseCommand
import pyodbc
from model_purch.models import Purch, PurchPay


class Command(BaseCommand):
    help = 'Импорт данных о закупках и графиках платежей из SQL Server'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Показать первые 10 строк без сохранения')
        parser.add_argument('--clear', action='store_true',
                            help='Очистить таблицы Purch и PurchPay перед импортом')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        clear = options['clear']

        # Подключение к SQL Server (Windows Authentication)
        conn_str = (
            'DRIVER={SQL Server};'
            'SERVER=vm-dwh;'
            'DATABASE=ModelPurch;'
            'Trusted_Connection=yes;'
        )

        sql = """
            SELECT 
                IIF([Purch] ='','Без поставщика',[Purch]) AS PurchName,
                lag_ AS lag_income,
                ТипПлатежа AS PurchPayName,
                ПроцентОплаты AS percent_pay,
                Дней AS lag_day_pay
            FROM 
            (
                SELECT 
                    [Purch],
                    120 AS lag_,
                    COALESCE(cu.ТипПлатежа, '') AS ТипПлатежа,
                    COALESCE(cu.Дней, 0) AS Дней,
                    COALESCE(cu.ПроцентОплаты, 0) AS ПроцентОплаты,
                    ROW_NUMBER() OVER(
                        PARTITION BY [Purch], COALESCE(cu.ТипПлатежа, '') 
                        ORDER BY [Purch]
                    ) AS RowID
                FROM [ModelPurch].[dbo].[PlanningGroupOZP] p
                LEFT JOIN DataWH.erp.[Справочники.ДоговорыКонтрагентов] c
                    ON p.Purch = c.Партнер
                LEFT JOIN DataWH.erp.[Справочник.ДоговорыКонтрагентов_Даичи_УсловияОплаты] cu
                    ON cu.Ссылка = c.Ссылка
            ) t
            WHERE RowID = 1
        """

        conn = None
        try:
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()

            self.stdout.write(f'Найдено записей: {len(rows)}')

            if dry_run:
                self.stdout.write(self.style.WARNING('DRY RUN — первые 10 строк:'))
                for i, row in enumerate(rows[:10], 1):
                    self.stdout.write(f'{i}. {row}')
                if len(rows) > 10:
                    self.stdout.write(f'... и ещё {len(rows) - 10} записей')
                return

            if clear:
                self.stdout.write(self.style.WARNING('Очистка таблиц Purch и PurchPay...'))
                PurchPay.objects.all().delete()
                Purch.objects.all().delete()

            purch_created = 0
            purch_updated = 0
            pay_created = 0
            pay_updated = 0
            skipped = 0

            # Группируем данные по PurchName
            purch_data = {}
            for row in rows:
                purch_name, lag_income, pay_name, percent_pay, lag_day_pay = row

                if purch_name not in purch_data:
                    purch_data[purch_name] = {
                        'lag_income': int(lag_income),
                        'payments': []
                    }

                # Добавляем платёж, если имя не пустое
                if pay_name and pay_name.strip():
                    purch_data[purch_name]['payments'].append({
                        'name': str(pay_name).strip(),
                        'percent_pay': float(percent_pay),
                        'lag_day_pay': int(lag_day_pay)
                    })

            # Обрабатываем каждую закупку
            for purch_name, data in purch_data.items():
                purch_name = str(purch_name).strip()
                if not purch_name:
                    skipped += 1
                    continue

                # Создаём или обновляем Purch
                try:
                    purch, created = Purch.objects.get_or_create(
                        name=purch_name,
                        defaults={'lag_income': data['lag_income']}
                    )

                    if not created:
                        # Обновляем lag_income, если изменился
                        if purch.lag_income != data['lag_income']:
                            purch.lag_income = data['lag_income']
                            purch.save()
                            purch_updated += 1
                        else:
                            purch_created += 1  # Считаем как существующий
                    else:
                        purch_created += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Ошибка при работе с Purch "{purch_name}": {e}'))
                    continue

                # Обрабатываем платежи
                for pay_data in data['payments']:
                    try:
                        pay, created = PurchPay.objects.get_or_create(
                            purch=purch,
                            name=pay_data['name'],
                            defaults={
                                'percent_pay': pay_data['percent_pay'],
                                'lag_day_pay': pay_data['lag_day_pay']
                            }
                        )

                        if not created:
                            # Обновляем, если изменились значения
                            if (pay.percent_pay != pay_data['percent_pay'] or
                                    pay.lag_day_pay != pay_data['lag_day_pay']):
                                pay.percent_pay = pay_data['percent_pay']
                                pay.lag_day_pay = pay_data['lag_day_pay']
                                pay.save()
                                pay_updated += 1
                        else:
                            pay_created += 1

                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Пропущено (ошибка платежа "{pay_data["name"]}" для {purch_name}): {e}'
                            )
                        )

            self.stdout.write(self.style.SUCCESS(
                f'Импорт завершён:\n'
                f'  ✅ Закупок создано: {purch_created}\n'
                f'  🔄 Закупок обновлено: {purch_updated}\n'
                f'  ✅ Платежей создано: {pay_created}\n'
                f'  🔄 Платежей обновлено: {pay_updated}\n'
                f'  ⚠️  Пропущено: {skipped}'
            ))

        except pyodbc.Error as e:
            self.stdout.write(self.style.ERROR(f'Ошибка подключения к SQL Server: {e}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка импорта: {e}'))
        finally:
            if conn is not None:
                conn.close()