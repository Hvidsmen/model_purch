"""CEO-only coefficient dialog and a single payout comparison request."""
import logging
from decimal import Decimal
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin
from base.access import role_for, ADMIN, DIRECTOR
from .models import Subdivision, PeriodMotivationCoefficient, PremiumDistribution
from .services import closing_details as details
from .services.closing_coefficient import confirm, period_closed


class Selection(forms.Form):
    year = forms.IntegerField(min_value=2000, max_value=2100)
    period = forms.TypedChoiceField(coerce=int, choices=PremiumDistribution._meta.get_field('period').choices)
    currency = forms.ChoiceField(choices=[('USD','USD'),('RUB','RUB')], required=False)


class Settings(forms.Form):
    coefficient = forms.DecimalField(min_value=0, max_digits=12, decimal_places=4, label='Общий коэффициент подразделения')
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    sales = forms.DecimalField(min_value=0, max_digits=12, decimal_places=4, required=False, label='Продажи')
    policies = forms.DecimalField(min_value=0, max_digits=12, decimal_places=4, required=False, label='Политики')
    directors = forms.DecimalField(min_value=0, max_digits=12, decimal_places=4, required=False, label='Директора')


class Person(forms.Form):
    kind = forms.ChoiceField(choices=[(k,k) for k in details.PERSONAL], label='Вид мотивации')
    author = forms.CharField(max_length=255, label='Полное ФИО', widget=forms.TextInput(attrs={'list':'closing-authors','autocomplete':'off'}))
    coefficient = forms.DecimalField(min_value=0, max_digits=12, decimal_places=4, label='Коэффициент')


People = forms.formset_factory(Person, extra=1, can_delete=True, max_num=1000, validate_max=True)
FIELDS = [('sales','Продажи'),('policies','Политики'),('directors','Директора')]


def authorize(user):
    if role_for(user,'motivation') not in (ADMIN,DIRECTOR):
        raise PermissionDenied


def selection(data):
    form = Selection(data)
    if not form.is_valid():
        raise ValidationError('Выберите год, период и валюту.')
    return form.cleaned_data['year'], form.cleaned_data['period'], form.cleaned_data.get('currency') or 'USD'


def baseline(year, period, currency):
    from .premium_distribution_views import latest_calculation
    from .services.main_quarter_mode import enabled
    from .services.execution_tracking import latest_for_report
    variant = 'main' if enabled() or period in (4,5) else 'all_2026'
    execution = latest_for_report(latest_calculation(variant), variant=variant, persist=False)
    if not execution or execution.state != 'completed' or execution.parameters.get('fact_year') != year:
        raise ValidationError('Нет завершённого расчёта за выбранный год. Сначала запустите расчёт.')
    return details.source_rows(execution, period, currency), execution


def settings(form, people):
    kinds = {kind:str(form.cleaned_data[field]) for field,kind in FIELDS if form.cleaned_data[field] is not None}
    persons = {}
    for row in people.cleaned_data:
        if not row or row.get('DELETE'):
            continue
        group = persons.setdefault(row['kind'], {})
        if any(details.identity(name)==details.identity(row['author']) for name in group):
            raise ValidationError('Для сотрудника можно задать только один персональный коэффициент.')
        group[row['author']] = str(row['coefficient'])
    return details.normalize_settings(kinds, persons)


@require_http_methods(['GET'])
def comparisons(request):
    authorize(request.user)
    try:
        year,period,currency = selection(request.GET)
        rows,execution = baseline(year,period,currency)
        records = {r.subdivision.subdivision_key:r for r in PeriodMotivationCoefficient.objects.filter(year=year,period=period).select_related('subdivision')}
        branches = Subdivision.objects.all()
        result = {}
        for branch in branches:
            record = records.get(branch.subdivision_key)
            source = [r for r in rows if r['branch'].casefold()==branch.subdivision_key.casefold()]
            comparison = details.impact(source, record.coefficient if record else 1,
                                        record.kind_coefficients if record else {}, record.personal_coefficients if record else {})
            result[str(branch.pk)] = {key:comparison[key] for key in ('before','after','change','coefficient','percent')}
        return JsonResponse(dict(rows=result, currency=currency, execution=str(execution.run_id)))
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)}, status=400)
    except Exception:
        logging.getLogger(__name__).exception('CEO payout comparison unavailable')
        return JsonResponse({'error':'Не удалось загрузить суммы к выплате. Проверьте доступность последнего расчёта.'}, status=503)


@xframe_options_sameorigin
@require_http_methods(['GET','POST'])
def dialog(request, subdivision_id):
    authorize(request.user)
    branch = get_object_or_404(Subdivision, pk=subdivision_id)
    data = request.POST if request.method=='POST' else request.GET
    try:
        year,period,currency = selection(data)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)}, status=400)
    record = PeriodMotivationCoefficient.objects.filter(subdivision=branch,year=year,period=period).first()
    locked = PremiumDistribution.objects.filter(subdivision=branch,year=year,period=period,state__in=['draft','completed']).exists()
    initial = dict(coefficient=record.coefficient if record else 1, revision=record.revision if record else 0)
    for field,kind in FIELDS:
        initial[field] = record.kind_coefficients.get(kind) if record else None
    persons = [dict(kind=kind,author=author,coefficient=value) for kind,members in (record.personal_coefficients.items() if record else []) for author,value in members.items()]
    form = Settings(request.POST if request.method=='POST' else None, initial=initial)
    people = People(request.POST if request.method=='POST' else None, initial=persons, prefix='people')
    source=[]; source_error=''; comparison=None; saved=False; execution=None
    try:
        raw,execution = baseline(year,period,currency)
        source = [r for r in raw if r['branch'].casefold()==branch.subdivision_key.casefold()]
    except ValidationError as exc:
        source_error=' '.join(exc.messages)
    except Exception:
        logging.getLogger(__name__).exception('CEO payout dialog source unavailable')
        source_error='Не удалось загрузить суммы расчёта. Настройки не сохранены; повторите позже.'
    if request.method=='POST' and form.is_valid() and people.is_valid():
        try:
            kinds,persons = settings(form,people)
            for kind,members in persons.items():
                known={details.identity(r['author']) for r in source if r['kind']==kind}
                known.update(details.identity(name) for name in (record.personal_coefficients.get(kind,{}) if record else {}))
                if not source_error and any(details.identity(name) not in known for name in members):
                    raise ValidationError('Сотрудник не найден в выбранном подразделении и виде мотивации. Выберите точное ФИО из расчёта.')
            comparison = details.impact(source,form.cleaned_data['coefficient'],kinds,persons) if not source_error else None
            if request.POST.get('action')=='preview':
                if source_error:raise ValidationError(source_error)
                return JsonResponse({'comparison':comparison})
            if source_error:raise ValidationError(source_error)
            if request.POST.get('action')!='save':raise ValidationError('Неизвестное действие.')
            confirm(request.user,branch,year,period,form.cleaned_data['coefficient'],form.cleaned_data['revision'],kind_coefficients=kinds,personal_coefficients=persons)
            saved=True
        except ValidationError as exc:
            form.add_error(None,exc)
    if request.method=='POST' and request.POST.get('action')=='preview':
        return JsonResponse({'error':'Проверьте коэффициенты и ФИО.','fields':form.errors,'people':people.errors},status=400)
    if comparison is None and not source_error:
        comparison = details.impact(source,record.coefficient if record else 1,record.kind_coefficients if record else {},record.personal_coefficients if record else {})
    return render(request,'admin_motivation/distribution/closing_dialog.html',dict(branch=branch,year=year,period=period,currency=currency,
        form=form,people=people,comparison=comparison,source_error=source_error,saved=saved,locked=locked,
        closed=period_closed(year,period),authors=sorted({r['author'] for r in source if r['author'] and r['kind'] in details.PERSONAL}),execution=execution))
