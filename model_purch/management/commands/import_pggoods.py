# model_purch/management/commands/import_pggoods.py
from django.core.management.base import BaseCommand
import pyodbc
from model_purch.models import PGGoods, KindPurch


class Command(BaseCommand):
    help = 'Импорт данных из SQL Server в PGGoods (с расчётной ценой DDP)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Показать первые 10 строк без сохранения')
        parser.add_argument('--clear', action='store_true',
                            help='Очистить таблицу PGGoods перед импортом')

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

        # Обновлённый SQL с LEFT JOIN на расчётную DDP
        sql = """
            SELECT 
                p.[PlanningGroupSalesERP],
                p.[PlanningKey],
                p.[Group_1],
                p.[BrandName],
                p.[PlanningGroupOZP],
                p.[Purch],
                p.[FlagInPlan],
                p.[Volume],
                ddp.PriceDDP
            FROM [ModelPurch].[dbo].[PlanningGroupOZP] p
            LEFT JOIN (
                SELECT 
                    mce.PlanningGroupOZPErp,
                    SUM(СуммаДДП) / SUM(Количество) AS PriceDDP
                FROM ModelPurch.dbo.PlanSalesERP pe
                INNER JOIN DataWH.dbo.ModelCodeERP mce
                    ON pe.Номенклатура = mce.ModelCode
                GROUP BY mce.PlanningGroupOZPErp
                HAVING SUM(Количество) > 0
            ) ddp
                ON p.PlanningGroupOZP = ddp.PlanningGroupOZPErp
            WHERE p.FlagInPlan = 1
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
                self.stdout.write(self.style.WARNING('Очистка таблицы PGGoods...'))
                PGGoods.objects.all().delete()

            created = 0
            updated = 0
            skipped = 0

            for row in rows:
                (
                    planning_sales, planning_key, group_1, brand_name,
                    planning_group_ozp, purch, flag_in_plan, volume,
                    price_ddp  # ← новое поле из LEFT JOIN
                ) = row

                # Вид закупки — жёстко задан
                kind_purch, _ = KindPurch.objects.get_or_create(
                    name='Закупается'
                )

                # Обработка PriceDDP: может быть NULL из-за LEFT JOIN
                ddp_usd_value = float(price_ddp) if price_ddp is not None else 0.0

                defaults = {
                    'planning_sales': str(planning_sales).strip() if planning_sales else '',
                    'planning_group': (
                        str(planning_group_ozp).strip() if planning_group_ozp
                        else str(planning_key).strip()
                    ),
                    'group_goods': (
                        str(group_1).strip() if group_1
                        else str(brand_name).strip()
                    ),
                    'kind_purch': kind_purch,
                    'brand': str(brand_name).strip() if brand_name else None,
                    'purch': str(purch).strip() if purch else None,
                    'volume': float(volume) if volume else 0.0,
                    'exw_usd': 0.0,
                    'ddp_usd': ddp_usd_value,  # ← заполняем из SQL
                    'kddp': 0.0,               # KDDP = DDP/EXW, но EXW=0, поэтому 0
                    'stock_cnt_day': 0,
                    'percent_stock_end': 0.0,
                }

                try:
                    goods = PGGoods.objects.get(
                        planning_group=defaults['planning_group'],
                        group_goods=defaults['group_goods'],
                        planning_sales=defaults['planning_sales'],
                    )
                    # Обновляем все поля, включая ddp_usd
                    for key, value in defaults.items():
                        setattr(goods, key, value)
                    goods.save()
                    updated += 1
                except PGGoods.DoesNotExist:
                    PGGoods.objects.create(**defaults)
                    created += 1
                except PGGoods.MultipleObjectsReturned:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Пропущено (дубликат): '
                            f'{defaults["planning_group"]} / {defaults["group_goods"]}'
                        )
                    )
                    skipped += 1

            self.stdout.write(self.style.SUCCESS(
                f'Импорт завершён:\n'
                f'  ✅ Создано: {created}\n'
                f'  🔄 Обновлено: {updated}\n'
                f'  ⚠️  Пропущено: {skipped}'
            ))

        except pyodbc.Error as e:
            self.stdout.write(self.style.ERROR(f'Ошибка подключения к SQL Server: {e}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка импорта: {e}'))
        finally:
            if conn is not None:
                conn.close()