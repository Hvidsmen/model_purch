"""Validate the submitted scope and read saved coefficient values back in the transaction."""
from decimal import Decimal, ROUND_HALF_EVEN
from math import isclose
from django.core.exceptions import ValidationError
from ..models import GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, SubdivisionManagerCoeff, KindManagerCoeff


class SaveVerification:
    def __init__(self, version, data, subdivision=None, employee=None, manager=False):
        from ..views import coefficient_number
        self.checks = []
        self.deletions = []
        self.changed = 0
        revision = data.get('revision')
        if revision is not None and str(version.revision) != revision:
            raise ValidationError('Версия изменилась в другой вкладке или другим пользователем. Изменения не сохранены. Обновите страницу и повторите правки.')
        core = SubdivisionCoeff.objects.filter(version=version, subdivision=subdivision) if subdivision else GlobalCoeff.objects.filter(version=version)
        directors = SubdivisionDirectorCoeff.objects.filter(version=version, subdivision=subdivision, employee=employee) if subdivision else GlobalDirectorCoeff.objects.filter(version=version)
        deleted = {int(key.split('=')[1]) for key in data if key.startswith('delete_coeff=')}
        if deleted:
            present = set(core.filter(goods_id__in=deleted).values_list('goods_id', flat=True)) | set(directors.filter(goods_id__in=deleted).values_list('goods_id', flat=True))
            if present != deleted:
                raise ValidationError('Удаляемые товары не найдены в выбранной версии. Изменения не сохранены.')
            self.deletions = [core.filter(goods_id__in=deleted), directors.filter(goods_id__in=deleted)]
            self.changed += sum(q.count() for q in self.deletions)
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
                    self.add(directors.filter(goods_id=good), 'k' + parts[2], value)
            elif key.startswith('select_var_calc=') and not manager:
                good = int(key.split('=')[1])
                if good not in deleted and data.get(f'original_var_calc={good}') != data[key]:
                    rows = core.filter(goods_id=good)
                    if not rows.exists():
                        raise ValidationError('Товар для изменения формулы не найден. Изменения не сохранены.')
                    for pk in rows.values_list('pk', flat=True):
                        self.add(core.filter(pk=pk), 'variation_calculate_id', int(data[key]))
            elif key.startswith('kind_coeff=') and manager:
                kind = int(key.split('=')[1])
                if not KindManagerCoeff.objects.filter(pk=kind).exists():
                    raise ValidationError('Период поправочного коэффициента не найден.')
                self.add(SubdivisionManagerCoeff.objects.filter(version=version, subdivision=subdivision, kind_id=kind), 'coeff', coefficient_number(data[key]))
        if not self.checks and not self.deletions:
            raise ValidationError('Нет коэффициентов для сохранения. Изменения не внесены.')

    @staticmethod
    def equal(actual, expected):
        if isinstance(expected, float):
            return actual is not None and isclose(float(actual), expected, rel_tol=0, abs_tol=1e-12)
        return actual == expected

    def add(self, query, field, expected):
        before = query.values_list(field, flat=True).first()
        self.changed += not self.equal(before, expected)
        self.checks.append((query, field, expected))

    def verify(self):
        for query, field, expected in self.checks:
            values = list(query.values_list(field, flat=True))
            if len(values) != 1 or not self.equal(values[0], expected):
                raise ValidationError('Проверка сохранения не пройдена. Все изменения этой операции отменены. Обновите страницу и повторите.')
        if any(query.exists() for query in self.deletions):
            raise ValidationError('Удаление коэффициентов не подтверждено. Изменения отменены.')
        return self.changed
