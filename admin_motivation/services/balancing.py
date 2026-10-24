"""Budget-neutral coefficient edits over a loaded, dated sales plan.

Terms retain the actual minimum fallback rather than freezing a coefficient's
weight before an edit. Every candidate is revalued after storage rounding.
"""
import hashlib
import json
from .subdivision_scope import included
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from ..models import (GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff,
                      SubdivisionDirectorCoeff, GlobalCoeffVersion)
from .comparison import coefficient_input
from .sales_plans import KINDS, SEGMENTS, number
from .subdivision_aliases import mapping, canonical, coefficient_rows, expand

ZERO = Decimal(0)
CENT = Decimal('.005')
MODES = {'type', 'subdivision', 'company'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class Budget:
    def __init__(self, version, plan, local=False, subdivision=None):
        self.version, self.plan, self.local = version, plan, local
        self.cells, self.terms, self.fallbacks = {}, [], 0
        names = mapping()
        target = canonical(subdivision.subdivision_key, names) if subdivision else None
        raw = []
        versions = [version.pk, *GlobalCoeffVersion.objects.filter(status='approved', effective_from__lt=version.effective_from).values_list('pk', flat=True)]
        model = SubdivisionCoeff if local else GlobalCoeff
        qs = model.objects.filter(version_id__in=versions).order_by('pk').select_related('goods', 'type_coeff', 'segment')
        if local:
            qs = qs.select_related('subdivision')
        for row in qs.iterator(chunk_size=2000):
            kind = row.type_coeff.type_coeff_name
            segment = row.segment.segment_name.upper().replace('K', 'O', 1)
            if kind not in KINDS or segment not in SEGMENTS:
                continue
            sub = row.subdivision.subdivision_key if local else ''
            raw.append((sub, 0, row.goods.planning_group_sales, row.goods.group, row.goods.brand, kind, segment,
                        self.cell(f'c:{row.pk}', row, kind, segment, sub, 0, coefficient_input(row.motivation_coeff)), row.version_id))
        director_model = SubdivisionDirectorCoeff if local else GlobalDirectorCoeff
        directors = director_model.objects.filter(version_id__in=versions).order_by('pk').select_related('goods')
        if local:
            directors = directors.select_related('subdivision', 'employee')
        for row in directors.iterator(chunk_size=2000):
            sub = row.subdivision.subdivision_key if local else ''
            staff = row.employee_id if local else 0
            for i in range(5):
                raw.append((sub, staff, row.goods.planning_group_sales, row.goods.group, row.goods.brand, 'Директор', f'O{i}',
                            self.cell(f'd:{row.pk}:{i}', row, 'Директор', f'O{i}', sub, staff, coefficient_input(getattr(row, f'k{i}'))), row.version_id))
        # Same merge precedence as the plan report; merged-out cells are never offered for editing.
        raw = list(coefficient_rows(raw, 0, [*range(7), 8]))
        active = {row[7] for row in raw}
        self.allowed = {row[7] for row in raw if row[8] == version.pk and (not local or target is None or row[0] == target)}
        self.cells = {key: cell for key, cell in self.cells.items() if key in active}
        exact, group_min, table_min, staff_by_sub = {}, defaultdict(list), defaultdict(list), defaultdict(set)
        for sub, staff, pg, group, brand, kind, segment, key, version_id in raw:
            if key not in self.cells:
                continue
            self.cells[key]['subdivision'] = sub
            identity = (sub, staff, pg, group, brand, kind, segment)
            if version_id == version.pk:
                if identity in exact:
                    raise ValidationError('Неоднозначные коэффициенты одной товарной классификации.')
                exact[identity] = (key,)
                group_min[(sub, staff, pg, kind)].append(key)
            # Policy fallback is the minimum of the selected table, as in comparison.Index.
            table_min[(sub if kind == 'Директор' else '', staff, kind)].append(key)
            if kind == 'Директор' and version_id == version.pk:
                staff_by_sub[sub].add(staff)
        # Global director rates apply separately to each director represented in branch coefficients.
        global_staff = defaultdict(set)
        if not local:
            for sub, staff in SubdivisionDirectorCoeff.objects.filter(version=version).values_list('subdivision__subdivision_key', 'employee_id').distinct():
                if included(sub, names):
                    global_staff[canonical(sub, names)].add(staff)
        input_hash = hashlib.sha256()
        input_count = 0
        plan_groups = {}
        lines = plan.lines.filter(plan_date__gte=version.effective_from).exclude(subdivision='').order_by('pk')
        if target is not None:
            lines = lines.filter(subdivision__in=expand(target, names))
        fields = ('pk', 'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand', 'segment_amounts')
        for raw_line in lines.values_list(*fields).iterator(chunk_size=2000):
            pk, day, original_sub, pg, group, brand, amounts = raw_line
            if not included(original_sub, names):
                continue
            sub = canonical(original_sub, names)
            input_hash.update(json.dumps(raw_line, sort_keys=True, default=str, separators=(',', ':')).encode())
            input_hash.update(b'\n')
            input_count += 1
            # The selected version is constant over the affected date range.
            # Resolve each branch/classification once rather than once per month.
            usd = plan_groups.setdefault((sub, pg, group, brand), [ZERO] * 5)
            for index, segment in enumerate(SEGMENTS):
                usd[index] += number(amounts.get(segment))
        for (sub, pg, group, brand), usd in plan_groups.items():
            lookup_sub = sub if local else ''
            kinds = [(kind, 0, 1) for kind in KINDS]
            kinds += [('Директор', staff, 1) for staff in sorted(staff_by_sub[sub])] if local else [('Директор', 0, len(global_staff[sub]))]
            for kind, staff, multiplier in kinds:
                if not multiplier or (kind == 'Директор' and not staff_by_sub[lookup_sub]):
                    continue
                for segment, amount in zip(SEGMENTS, usd):
                    if not amount:
                        continue
                    candidates = exact.get((lookup_sub, staff, pg, group, brand, kind, segment))
                    if candidates is None:
                        candidates = group_min.get((lookup_sub, staff, pg, kind)) or table_min.get((lookup_sub if kind == 'Директор' else '', staff, kind))
                        self.fallbacks += 1
                    if not candidates:
                        raise ValidationError(f'Нет коэффициентов: {sub}, {pg}, {kind}. Заполните матрицу коэффициентов.')
                    self.terms.append((sub, kind, tuple(candidates), amount * multiplier))
        if not input_count:
            raise ValidationError('В плане нет строк подразделений с даты выбранной версии. Выберите другой план или дату версии.')
        # Compact duplicate plan terms so iterations do not scan the entire plan.
        grouped = defaultdict(Decimal)
        for sub, kind, candidates, amount in self.terms:
            grouped[(sub, kind, candidates)] += amount
        self.terms = [(*key, value) for key, value in grouped.items() if value]
        self.fingerprint = digest({'cells': self.cells, 'lines': input_hash.hexdigest(), 'terms': self.terms,
                                   'version': [version.pk, version.revision, version.status, version.effective_from],
                                   'plan': [plan.pk, plan.loaded_at], 'aliases': names})

    def cell(self, key, row, kind, segment, sub, staff, value):
        self.cells[key] = {'id': key, 'value': str(value), 'kind': kind, 'segment': segment.replace('O', 'K', 1),
                           'subdivision': sub, 'employee': str(row.employee) if staff else '', 'employee_key': staff,
                           'goods': str(row.goods), 'planning_group': row.goods.planning_group_sales,
                           'group': row.goods.group, 'brand': row.goods.brand}
        return key

    def totals(self, values):
        result = defaultdict(Decimal)
        for sub, kind, candidates, amount in self.terms:
            result[(sub, kind)] += min(values[key] for key in candidates) * amount
        return dict(result)

    def payload(self):
        values = {key: Decimal(cell['value']) for key, cell in self.cells.items()}
        # Contribution for minimum fallbacks is attributed equally among tied providers for display only.
        exposure = defaultdict(Decimal)
        for sub, kind, candidates, amount in self.terms:
            minimum = min(values[key] for key in candidates)
            providers = [key for key in candidates if values[key] == minimum]
            for key in providers:
                exposure[key] += amount / len(providers)
        totals = self.totals(values)
        return {'cells': [dict(cell, plan_amount=str(exposure[key]), premium=str(exposure[key] * values[key]))
                          for key, cell in self.cells.items() if key in self.allowed], 'fingerprint': self.fingerprint,
                'fallbacks': self.fallbacks, 'editable': self.version.is_editable,
                'totals': summary(totals, totals),
                'display_totals': summary(totals, totals, company=not self.local)}

    def balance(self, manual, compensate, mode):
        if mode not in MODES or (mode == 'subdivision' and not self.local):
            raise ValidationError('Выберите допустимый режим сохранения премии.')
        if not isinstance(manual, dict) or not isinstance(compensate, list) or not manual:
            raise ValidationError('Укажите ручные изменения и выберите коэффициенты для компенсации.')
        pool = set(compensate)
        if any(not isinstance(key, str) for key in pool) or (set(manual) | pool) - self.allowed:
            raise ValidationError('Выбран коэффициент другой версии или области балансировки.')
        if set(manual) & pool:
            raise ValidationError('Ручные значения нельзя одновременно использовать для компенсации.')
        if not pool:
            raise ValidationError('Выберите хотя бы один коэффициент для компенсации.')
        before = {key: Decimal(cell['value']) for key, cell in self.cells.items()}
        values = dict(before)
        for key, value in manual.items():
            values[key] = stored(key, coefficient_input(value))
        if all(values[key] == before[key] for key in manual):
            raise ValidationError('Ручные значения не изменились.')
        group = lambda sub, kind: kind if mode == 'type' else sub if mode == 'subdivision' else 'Компания'
        constraints = defaultdict(list)
        for term in self.terms:
            constraints[group(term[0], term[1])].append(term)
        # A global coefficient can influence multiple branches: only company/type modes are offered there.
        for label, terms in constraints.items():
            relevant = {key for _, _, candidates, _ in terms for key in candidates}
            selected = relevant & pool
            def total(candidate):
                return sum((min(candidate[key] for key in candidates) * amount for _, _, candidates, amount in terms), ZERO)
            target = total(before)
            if abs(total(values) - target) < CENT:
                continue
            adjustable = [key for key in selected if before[key]]
            if not adjustable:
                raise ValidationError(f'{label}: нет выбранных ненулевых коэффициентов, влияющих на премию, для компенсации.')
            adjustable = set(adjustable)
            upper = min(Decimal(1) / abs(before[key]) for key in adjustable)
            # Only terms depending on a selected compensator vary during search.
            # Keep minimum fallbacks dynamic; all other contributions are constant.
            constant = ZERO
            moving = defaultdict(Decimal)
            for _, _, candidates, amount in terms:
                changing = tuple(key for key in candidates if key in adjustable)
                if not changing:
                    constant += min(values[key] for key in candidates) * amount
                    continue
                fixed = [values[key] for key in candidates if key not in adjustable]
                moving[(changing, min(fixed) if fixed else None)] += amount
            moving = [(keys, fixed, amount) for (keys, fixed), amount in moving.items() if amount]
            variable_target = target - constant

            def candidate(factor):
                return {key: stored(key, before[key] * factor) for key in adjustable}

            def error(proposed):
                result = ZERO
                for keys, fixed, amount in moving:
                    value = min(proposed[key] for key in keys)
                    if fixed is not None:
                        value = min(value, fixed)
                    result += value * amount
                return result - variable_target

            # With exact coefficients (or a minimum consisting entirely of
            # compensators), the budget is linear in the common multiplier.
            direct = None
            if all(fixed is None for _, fixed, _ in moving):
                slope = sum((min(before[key] for key in keys) * amount for keys, _, amount in moving), ZERO)
                if slope:
                    factor = variable_target / slope
                    if ZERO <= factor <= upper:
                        direct = candidate(factor)
                        if abs(error(direct)) < CENT:
                            values.update(direct)
                            continue
            low, high = ZERO, upper
            left, right = candidate(low), candidate(high)
            fleft, fright = error(left), error(right)
            if fleft * fright > 0:
                raise ValidationError(f'{label}: выбранными коэффициентами нельзя сохранить бюджет в диапазоне −100%…100%. Расширьте выбор или уменьшите ручное изменение.')
            best, best_error = (left, abs(fleft)) if abs(fleft) <= abs(fright) else (right, abs(fright))
            if direct is not None and abs(error(direct)) < best_error:
                best, best_error = direct, abs(error(direct))
            for _ in range(90):
                mid = (low + high) / 2
                proposed = candidate(mid)
                delta = error(proposed)
                if abs(delta) < best_error:
                    best, best_error = proposed, abs(delta)
                if delta == 0:
                    best, best_error = proposed, ZERO
                    break
                if delta * fleft > 0:
                    low, fleft = mid, delta
                else:
                    high = mid
            if best_error >= CENT:
                raise ValidationError(f'{label}: точность хранения коэффициентов не позволяет сохранить премию до цента. Выберите больше коэффициентов для компенсации.')
            values.update(best)
        for label, terms in constraints.items():
            delta = sum(((min(values[key] for key in candidates) - min(before[key] for key in candidates)) * amount
                         for _, _, candidates, amount in terms), ZERO)
            if abs(delta) >= CENT:
                raise ValidationError(f'{label}: взаимосвязанные минимальные коэффициенты нарушают баланс. Выберите другой набор или режим по компании.')
        old, new = self.totals(before), self.totals(values)
        changes = {key: {'before': str(before[key]), 'after': str(value),
                         'before_percent': str(before[key] * 100), 'after_percent': str(value * 100),
                         'label': ' · '.join(str(self.cells[key][field]) for field in ('subdivision', 'employee', 'planning_group', 'group', 'brand', 'kind', 'segment') if self.cells[key][field]),
                         'source': 'manual' if key in manual else 'auto'}
                   for key, value in values.items() if value != before[key]}
        if not changes:
            raise ValidationError('Нет изменений для сохранения.')
        return {'changes': changes, 'totals': summary(old, new),
                'display_totals': summary(old, new, company=not self.local), 'mode': mode,
                'before': str(sum(old.values(), ZERO)), 'after': str(sum(new.values(), ZERO)),
                'delta': str(sum(new.values(), ZERO) - sum(old.values(), ZERO))}


def stored(key, value):
    # Director columns have six decimal places; float cells round-trip through Python's storage representation.
    if key.startswith('d:'):
        return value.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
    return Decimal(str(float(value.quantize(Decimal('.000000000001'), rounding=ROUND_HALF_UP))))


def summary(before, after, company=False):
    if company:
        def aggregate(values):
            result = defaultdict(Decimal)
            for (subdivision, kind), value in values.items():
                result[('Компания', kind)] += value
            return dict(result)
        before, after = aggregate(before), aggregate(after)
    return [{'subdivision': sub, 'kind': kind, 'before': str(before.get((sub, kind), ZERO)),
             'after': str(after.get((sub, kind), ZERO)),
             'delta': str(after.get((sub, kind), ZERO) - before.get((sub, kind), ZERO))}
            for sub, kind in sorted(before.keys() | after.keys())]
