"""FAO edits are explicit, branch-scoped POST operations with optimistic revisions."""
import logging
from datetime import date
from urllib.parse import urlencode
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Subdivision, FaoReview, FaoDocument, FaoCorrection
from .services import fao_review as service
from .services.fao_documents import lookup, summary


class Selection(forms.Form):
    subdivision=forms.ModelChoiceField(queryset=Subdivision.objects.all(),label='Подразделение')
    year=forms.IntegerField(min_value=2000,max_value=2100,label='Год')
    quarter=forms.TypedChoiceField(coerce=int,choices=[(q,f'{q} квартал') for q in range(1,5)],label='Квартал корректировки')


class DocumentSearch(forms.Form):
    number=forms.CharField(max_length=255,label='Номер документа')
    document_date=forms.DateField(label='Дата документа (отгрузки)',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))


class Adjustment(forms.Form):
    review=forms.IntegerField(widget=forms.HiddenInput)
    revision=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    document=forms.IntegerField(widget=forms.HiddenInput)
    new_amount=forms.CharField(required=False,label='Новая сумма документа, USD',widget=forms.TextInput(attrs={'inputmode':'decimal','placeholder':'0,00'}))
    correction_date=forms.DateField(label='Дата корректировки',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    excluded=forms.BooleanField(required=False,label='Исключить из ОЗС — обнулить факт и премию корректировкой')

    def clean_new_amount(self):
        value=self.cleaned_data.get('new_amount','')
        return service.cash(value) if value else None

    def clean(self):
        data=super().clean()
        if not data.get('excluded') and data.get('new_amount') is None:self.add_error('new_amount','Укажите новую сумму или исключение.')
        return data


@require_http_methods(['GET','POST'])
def page(request):
    service.authorize(request.user)
    today=timezone.localdate();data=request.POST if request.method=='POST' else request.GET
    selection=Selection(data if 'subdivision' in data else None,initial={'year':today.year,'quarter':(today.month-1)//3+1})
    review=None;document=None;document_info=None;ready_error='';history=[];preview=None;payment_closure=None
    search=DocumentSearch(request.POST if request.POST.get('action')=='lookup' else None)
    adjustment=None
    if selection.is_valid():
        branch,year,quarter=(selection.cleaned_data[k] for k in ('subdivision','year','quarter'))
        review=service.current(branch,year,quarter)
        from .services.period_closure import closure
        payment_closure=closure(branch,year,quarter)
        query={'subdivision':branch.pk,'year':year,'quarter':quarter}
        target=reverse('motivation_fao_review')+'?'+urlencode(query)
        if request.method=='POST':
            action=request.POST.get('action')
            try:
                if action=='begin':
                    service.begin(request.user,branch,year,quarter,request.POST.get('reason',''))
                    messages.success(request,'Коррекция подразделения открыта. Прежние коэффициенты и назначения сохранены в истории.')
                    return redirect(target)
                elif action in ('finish','recalculate'):
                    selected=get_object_or_404(FaoReview,pk=request.POST.get('review'),subdivision=branch,year=year,quarter=quarter)
                    if action=='finish':
                        selected=service.finish(request.user,selected.pk,int(request.POST.get('revision','0')))
                        messages.success(request,'Проверка ФАО завершена. После нового расчёта гендиректор должен повторно подтвердить коэффициенты.')
                    try:
                        service.recalculate(request.user,selected)
                        messages.success(request,'Пересчёт запущен. Обновите страницу после завершения.')
                    except Exception as exc:
                        logging.getLogger(__name__).exception('FAO calculation start failed')
                        messages.error(request,'Проверка сохранена, но пересчёт не запущен. '+(' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте журнал задания и повторите запуск.'))
                    return redirect(target)
                elif action=='lookup' and search.is_valid():
                    if not review or review.state!='open':raise ValidationError('Сначала откройте коррекцию подразделения.')
                    document=lookup(branch,**search.cleaned_data)
                elif action in ('adjust','preview'):
                    adjustment=Adjustment(request.POST)
                    if request.POST.get('document','').isdigit():document=get_object_or_404(FaoDocument,pk=request.POST['document'],subdivision=branch)
                    if adjustment.is_valid():
                        values=adjustment.cleaned_data
                        selected=get_object_or_404(FaoReview,pk=values['review'],subdivision=branch,year=year,quarter=quarter)
                        document=get_object_or_404(FaoDocument,pk=values['document'],subdivision=branch)
                        if action=='preview':
                            if selected.revision!=values['revision']:raise ValidationError('Проверка изменена. Обновите страницу.')
                            preview=service.preview(request.user,selected,document,values.get('new_amount'),values['correction_date'],values['excluded'])
                        else:
                            service.adjust(request.user,selected.pk,values['revision'],document.pk,values.get('new_amount'),values['correction_date'],values['excluded'])
                            messages.success(request,'Дельта факта и премий сохранена новой датой. Ставки взяты из исходной ОЗС.')
                            return redirect(target+'&document='+str(document.pk))
                else:
                    if action not in ('lookup','adjust','preview'):raise ValidationError('Неизвестное действие.')
            except Http404:
                raise
            except (ValidationError,ValueError) as exc:
                messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия проверки.')
            except Exception:
                logging.getLogger(__name__).exception('FAO review failed')
                messages.error(request,'Операция не выполнена. Проверьте доступность исходного расчёта и повторите.')
        elif request.GET.get('document'):
            document=get_object_or_404(FaoDocument,pk=request.GET['document'],subdivision=branch)
        if review:
            history=FaoCorrection.objects.filter(review__subdivision=branch,review__year=year,review__quarter=quarter).select_related('document','review','actor').order_by('-pk')
            if review.state=='completed':
                try:service.ensure_ready(branch,year,quarter)
                except Exception as exc:ready_error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Не удалось проверить завершение расчёта.'
        if document:
            document_info=summary(document)
            last=document.corrections.order_by('-pk').first()
            document_info['current_amount']=last.new_amount if last else document.original_amount
            if adjustment is None:
                adjustment=Adjustment(initial={'document':document.pk,'review':review.pk if review else 0,'revision':review.revision if review else 0,
                    'new_amount':document_info['current_amount'],'correction_date':today})
    if adjustment is not None and review is not None:
        from calendar import monthrange
        month=(review.quarter-1)*3+1
        adjustment.fields['correction_date'].widget.attrs.update(min=date(review.year,month,1).isoformat(),max=date(review.year,month+2,monthrange(review.year,month+2)[1]).isoformat())
    return render(request,'admin_motivation/fao_review.html',dict(selection=selection,review=review,search=search,adjustment=adjustment,
        document_info=document_info,preview=preview,history=history,ready_error=ready_error,payment_closure=payment_closure,skip_motivation_workflow=True))


@require_http_methods(['POST'])
def paid(request,pk):
    service.authorize(request.user)
    from .models import PremiumDistribution
    from .services.period_closure import mark_paid
    get_object_or_404(PremiumDistribution,pk=pk)
    try:
        if request.POST.get('payments_made')!='yes':raise ValidationError('Подтвердите, что выплаты произведены.')
        mark_paid(request.user,pk,int(request.POST.get('revision','0')))
        messages.success(request,'Выплаты произведены. Период подразделения закрыт ФАО.')
    except (ValidationError,ValueError) as exc:
        messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия распределения.')
    return redirect('motivation_distribution_detail',pk=pk)
