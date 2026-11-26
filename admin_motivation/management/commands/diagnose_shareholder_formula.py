"""Read immutable rate/result keys without reading private SQL or changing runs."""
import json
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError

from admin_motivation.models import ShareholderCalculation,SubdivisionCoeff
from admin_motivation.services import shareholder_reports as reports


class Command(BaseCommand):
    help='Показать настройки и формулы товара в сохранённом акционерском запуске (только чтение).'

    def add_arguments(self,parser):
        parser.add_argument('--run',help='UUID запуска; по умолчанию последний завершённый запуск.')
        parser.add_argument('--subdivision',default='Д-Питер')
        parser.add_argument('--kind',choices=list(reports.KINDS),default='Политики')
        parser.add_argument('--planning',default='1. RAC')
        parser.add_argument('--goods',default='Мобильные кондиционеры')

    def handle(self,*args,**options):
        runs=ShareholderCalculation.objects.filter(state='completed')
        try:
            run=runs.get(pk=options['run']) if options['run'] else runs.order_by('-created_at','-pk').first()
        except (ShareholderCalculation.DoesNotExist,ValidationError,ValueError) as error:
            raise CommandError('Завершённый запуск не найден: '+str(options['run'])) from error
        if run is None:raise CommandError('Нет завершённых акционерских запусков.')
        branch,kind,planning,goods=(options[k] for k in ('subdivision','kind','planning','goods'))
        def same(a,b):return str(a or '').strip().casefold()==str(b or '').strip().casefold()
        def relevant(row):
            return (same(row.get('Subdivision'),branch) and same(row.get('PGSales'),planning)
                    and (same(row.get('Group_'),goods) or str(row.get('Group_') or '').strip() in ('','*')))
        rates=run.configuration.get('regional_rates',{})
        result={'run':str(run.pk),'created_at':run.created_at,'completed_at':run.completed_at,
                'fact_through':run.as_of,'subdivision':branch,'kind':kind,'planning':planning,'goods':goods,
                'regional_key_format':rates.get('key_format','legacy / not recorded'),
                'frozen_versions':rates.get('versions',[]),'frozen_defaults':rates.get('defaults',[]),
                'frozen_raw_coefficients':[row for row in rates.get('coefficients',[]) if relevant(row)],
                'saved_sql_coefficients':[row for row in reports.records(run,'policy_rates') if relevant(row)],
                'saved_sql_defaults':list(reports.records(run,'policy_defaults'))}
        # Current portal settings are a comparison, never substituted into the
        # immutable result. Keep version dates so a later edit is distinguishable.
        current=SubdivisionCoeff.objects.filter(version__status='approved',subdivision__subdivision_key=branch,
            type_coeff__type_coeff_name=kind,goods__planning_group_sales=planning,goods__group__in=[goods,'*',''])
        result['current_approved_cells']=list(current.order_by('version__effective_from','goods_id','segment_id').values(
            'version_id','version__effective_from','version__revision','goods__goods_key','goods__group','goods__brand',
            'segment__segment_name','variation_calculate__variation_name','motivation_coeff'))
        grouped=defaultdict(lambda:{'rows':0,'possible_USD':Decimal(0),'paid_USD':Decimal(0)})
        for row in reports.records(run,'report_'+reports.KINDS[kind]):
            if not all(same(row.get(field),value) for field,value in [('Подразделение',branch),('ГруппаПлановПродаж',planning),('ГруппаТоваров',goods)]):continue
            key=tuple(str(row.get(field) or '') for field in ('КварталМотивации','Марка','VariationCalculate'))
            item=grouped[key];item['rows']+=1
            for target,field in [('possible_USD','СуммаМотивацииUSD'),('paid_USD','СуммаМотивацииUSDУчетПроцента')]:
                if row.get(field) is not None:item[target]+=Decimal(str(row[field]))
        result['saved_result']=[dict(period=key[0],brand=key[1],formula=key[2],**value) for key,value in sorted(grouped.items())]
        result['note']='Версии и ставки выше относятся к сохранённому запуску; current_approved_cells — текущие настройки. Квартальные и годовые суммы показаны раздельно.'
        self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2,default=str))
