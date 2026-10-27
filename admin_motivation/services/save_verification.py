"""Validate the submitted scope and read saved coefficient values back in the transaction."""
from decimal import Decimal, ROUND_HALF_EVEN
from math import isclose
from django.core.exceptions import ValidationError
from ..models import GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, SubdivisionManagerCoeff, KindManagerCoeff


class SaveVerification:
    def __init__(self, version, data, subdivision=None, employee=None, manager=False, actor=None):
        from ..views import coefficient_number
        self.version, self.subdivision, self.employee, self.actor = version, subdivision, employee, actor
        self.revision_before = version.revision
        self.audit_changes = []
        self.checks = []
        self.deletions = []
        self.changed = 0
        self.goods = set()
        self.groups = {}
        revision = data.get('revision')
        if revision is not None and str(version.revision) != revision:
            raise ValidationError('Версия изменилась в другой вкладке или другим пользователем. Изменения не сохранены. Обновите страницу и повторите правки.')
        core = SubdivisionCoeff.objects.filter(version=version, subdivision=subdivision) if subdivision else GlobalCoeff.objects.filter(version=version)
        directors = SubdivisionDirectorCoeff.objects.filter(version=version, subdivision=subdivision, employee=employee) if subdivision else GlobalDirectorCoeff.objects.filter(version=version)
        existing_director_goods = set(directors.values_list('goods_id',flat=True))
        nonzero_director_goods = set()
        for key in data:
            parts = key.split('=')
            if len(parts)==3 and parts[0]=='director_coeff' and parts[1].isdigit() and coefficient_number(data[key]) != 0:
                nonzero_director_goods.add(int(parts[1]))
        deleted = {int(key.split('=')[1]) for key in data if key.startswith('delete_coeff=')}
        if deleted:
            present = set(core.filter(goods_id__in=deleted).values_list('goods_id', flat=True)) | set(directors.filter(goods_id__in=deleted).values_list('goods_id', flat=True))
            if present != deleted:
                raise ValidationError('Удаляемые товары не найдены в выбранной версии. Изменения не сохранены.')
            self.deletions = [core.filter(goods_id__in=deleted), directors.filter(goods_id__in=deleted)]
            self.changed += sum(q.count() for q in self.deletions)
            for query in self.deletions:
                for row in query.values():
                    self.audit_changes.append({'model':query.model.__name__, 'pk':row['id'], 'goods_id':row.get('goods_id'), 'field':'delete', 'before':{key:str(value) if value is not None else None for key,value in row.items()}, 'after':None})
        from .editor_patch import formulas
        formula_core,formula_directors = formulas(version,data,subdivision,employee) if not manager else ({},{})
        prefix = 'sub_coeff=' if subdivision else 'global_coeff='
        for key in data:
            if key.startswith(prefix) and not manager:
                query = core.filter(pk=int(key.split('=')[1]))
                if not query.exists():
                    raise ValidationError('Коэффициент не найден в выбранной версии и подразделении. Изменения не сохранены.')
                if query.first().goods_id not in deleted:
                    self.add(query, 'motivation_coeff', coefficient_number(data[key]))
            elif key.startswith('director_coeff=') and not manager:
                parts = key.split('=')
                if len(parts) != 3 or parts[2] not in '01234' or len(parts[2]) != 1:
                    raise ValidationError('Некорректная ячейка директора.')
                if subdivision and (not employee or employee.is_archived or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
                    raise ValidationError('Директор удалён или больше не привязан к подразделению. Изменения не сохранены.')
                good = int(parts[1])
                if good not in deleted:
                    value = Decimal(str(coefficient_number(data[key]))).quantize(Decimal('.000001'), rounding=ROUND_HALF_EVEN)
                    if good not in existing_director_goods and good not in nonzero_director_goods:
                        continue  # An unchanged zero placeholder is not a request to create a rate.
                    self.add(directors.filter(goods_id=good), 'k' + parts[2], value, goods_id=good)
            elif key.startswith('select_var_calc=') and not manager:
                good = int(key.split('=')[1])
                if good not in deleted and data.get(f'original_var_calc={good}') != data[key]:
                    rows = core.filter(goods_id=good)
                    if not rows.exists():
                        raise ValidationError('Товар для изменения формулы не найден. Изменения не сохранены.')
                    for pk in rows.values_list('pk', flat=True):
                        if f'formula_coeff={pk}' in data:continue
                        self.add(core.filter(pk=pk), 'variation_calculate_id', int(data[key]))
            elif key.startswith('kind_coeff=') and subdivision is not None:
                kind = int(key.split('=')[1])
                if not KindManagerCoeff.objects.filter(pk=kind).exists():
                    raise ValidationError('Период поправочного коэффициента не найден.')
                self.add(SubdivisionManagerCoeff.objects.filter(version=version, subdivision=subdivision, kind_id=kind), 'coeff', coefficient_number(data[key]))
        for pk,value in formula_core.items():
            self.add(core.filter(pk=pk),'variation_calculate_id',value)
        for good,value in formula_directors.items():
            self.add(directors.filter(goods_id=good),'variation_calculate_id',value,goods_id=good)
        if not self.checks and not self.deletions:
            raise ValidationError('Нет коэффициентов для сохранения. Изменения не внесены.')

    @staticmethod
    def equal(actual, expected):
        if isinstance(expected, float):
            return actual is not None and isclose(float(actual), expected, rel_tol=0, abs_tol=1e-12)
        return actual == expected

    def add(self, query, field, expected, goods_id=None):
        columns = ['pk', field] + (['goods_id'] if hasattr(query.model, 'goods') else [])
        if hasattr(query.model,'type_coeff'):
            columns += ['type_coeff__type_coeff_name','segment__segment_name']
        elif hasattr(query.model,'kind'):
            columns += ['kind__name']
        source = query.values(*columns).first()
        before = source[field] if source else None
        different = not self.equal(before, expected)
        self.changed += different
        if different:
            self.audit_changes.append({'model':query.model.__name__, 'pk':source['pk'] if source else None, 'goods_id':source.get('goods_id') if source else goods_id, 'kind':source.get('type_coeff__type_coeff_name','Директор' if 'Director' in query.model.__name__ else 'Поправки') if source else 'Директор', 'segment':source.get('segment__segment_name',source.get('kind__name',field.upper())) if source else field.upper(), 'field':field, 'before':str(before) if before is not None else None, 'after':str(expected)})
            model = query.model.__name__
            label = 'Директор' if 'Director' in model else 'Поправки' if 'Manager' in model else query.values_list('type_coeff__type_coeff_name',flat=True).first() or 'Ставки'
            self.groups[label] = self.groups.get(label,0) + 1
            if 'Manager' not in model:
                good=query.values_list('goods_id',flat=True).first()
                if good is not None: self.goods.add(good)
        self.checks.append((query, field, expected))

    def verify(self):
        for query, field, expected in self.checks:
            values = list(query.values_list(field, flat=True))
            if len(values) != 1 or not self.equal(values[0], expected):
                raise ValidationError('Проверка сохранения не пройдена. Все изменения этой операции отменены. Обновите страницу и повторите.')
        if any(query.exists() for query in self.deletions):
            raise ValidationError('Удаление коэффициентов не подтверждено. Изменения отменены.')
        if self.actor is not None and self.changed:
            from ..models import CoefficientEditLog, GlobalCoeffVersion, Goods
            labels={good.pk:' / '.join([good.planning_group_sales or '—',good.group or '—',good.brand or '—']) for good in Goods.objects.filter(pk__in={change['goods_id'] for change in self.audit_changes if change.get('goods_id')})}
            for change in self.audit_changes:
                change['goods_label']=labels.get(change.get('goods_id'),'Поправки руководителя')
            CoefficientEditLog.objects.create(version=self.version, subdivision=self.subdivision, employee=self.employee,
                actor=self.actor, actor_label=(self.actor.get_full_name() or self.actor.get_username())[:255],
                scope_label=((str(self.subdivision) if self.subdivision else 'Глобальные коэффициенты') + (' · '+str(self.employee) if self.employee else ''))[:512],
                revision_before=self.revision_before, revision_after=GlobalCoeffVersion.objects.get(pk=self.version.pk).revision,
                changed_count=self.changed, changes=self.audit_changes)
        return self.changed

    def summary(self):
        parts = [f'{label}: {count}' for label,count in sorted(self.groups.items())]
        if self.goods: parts.append(f'товаров: {len(self.goods)}')
        return '; '.join(parts)
