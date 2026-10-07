from django.shortcuts import render
from .forms import *


def index(request):
    return render(request, "PlanningSystem/index.html", )


def load_plan_by_chanel(request):
    if request.method == 'POST':
        form_ = PlanByChanelForm(request.POST, request.FILES)
        sheet_name = request.POST['sheet_name']
        file = request.FILES['file']
        period_id = request.POST['period']
        option_id = request.POST['option']
        year = request.POST['year']

        period_obj = PeriodPlanRef.objects.get(id=period_id)
        option_obj = OptionPlanRef.objects.get(id=option_id)

        if len(PlanSalesByChanelHeader.objects.filter(opition_plan=option_obj)) != 0:
            plan = PlanSalesByChanelHeader.objects.get(opition_plan=option_obj)
            plan.delete()

        plan = PlanSalesByChanelHeader.objects.create(
            opition_plan=option_obj
            , file=file
            , sheet_name=sheet_name
            , period=period_obj
            , year=year
        )
        plan.save()
        PlanSalesByChanelGoods.create_by_file(plan)

    form = PlanByChanelForm()
    return render(
        request
        , "PlanningSystem/load_plan_chanel.html", {
            'form': form
        }
    )


def _load_percentage(request, header_model, goods_model, template_id, template_name):
    templates_file = TemplatesFile.objects.filter(id=template_id).first()
    form = PercentageSubdivisionForm(
        request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None,
    )
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        from django.db import transaction

        with transaction.atomic():
            header_model.objects.filter(opition_plan=data['option']).delete()
            header = header_model.objects.create(
                opition_plan=data['option'], file=data['file'],
                sheet_name=data['sheet_name'], period=data['period'], year=data['year'],
            )
            goods_model.create_by_file(header)
    return render(request, template_name, {'form': form, 'templates_file': templates_file})


def load_percentage_subdivision(request):
    return _load_percentage(
        request, PercentSubdivisionHeader, PecentSubdivisionGoods, 1,
        'PlanningSystem/load_percentage_subdivision.html',
    )


def split_model(request):
    if request.method == 'POST':
        form = SplitModelForm(request.POST, request.FILES)
        sheet_name = request.POST['sheet_name']
        file = request.FILES['file']
        option_id = request.POST['option']
        year = request.POST['year']

        option_obj = OptionPlanRef.objects.get(id=option_id)

    form = SplitModelForm()
    return render(
        request
        , "PlanningSystem/split_model.html", {
            'form': form
        }
    )


def load_percent_season(request):
    return _load_percentage(
        request, PercentSubdivisionSeason, PercentSubdivisionSeason, 2,
        'PlanningSystem/load_percent_season.html',
    )
