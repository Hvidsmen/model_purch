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


def load_percentage_subdivision(request):
    templates_file = TemplatesFile.objects.get(id=1)
    if request.method == 'POST':
        form = PercentageSubdivisionForm(request.POST, request.FILES)
        sheet_name = request.POST['sheet_name']
        file = request.FILES['file']
        period_id = request.POST['period']
        option_id = request.POST['option']
        year = request.POST['year']

        period_obj = PeriodPlanRef.objects.get(id=period_id)
        option_obj = OptionPlanRef.objects.get(id=option_id)

        if len(PercentSubdivisionHeader.objects.filter(opition_plan=option_obj)) != 0:
            plan = PercentSubdivisionHeader.objects.get(opition_plan=option_obj)
            plan.delete()

        plan = PercentSubdivisionHeader.objects.create(
            opition_plan=option_obj
            , file=file
            , sheet_name=sheet_name
            , period=period_obj
            , year=year
        )
        plan.save()
        PecentSubdivisionGoods.create_by_file(plan)

    form = PercentageSubdivisionForm()
    return render(
        request
        , "PlanningSystem/load_percentage_subdivision.html", {
            'form': form
        , 'templates_file':templates_file
    }
    )

    # Create your views here.

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


def load_percentage_subdivision(request):
    templates_file = TemplatesFile.objects.get(id=2)
    if request.method == 'POST':
        form = PercentageSubdivisionForm(request.POST, request.FILES)
        sheet_name = request.POST['sheet_name']
        file = request.FILES['file']
        period_id = request.POST['period']
        option_id = request.POST['option']
        year = request.POST['year']

        period_obj = PeriodPlanRef.objects.get(id=period_id)
        option_obj = OptionPlanRef.objects.get(id=option_id)

        if len(PercentSubdivisionSeason.objects.filter(opition_plan=option_obj)) != 0:
            plan = PercentSubdivisionSeason.objects.get(opition_plan=option_obj)
            plan.delete()

        plan = PercentSubdivisionSeason.objects.create(
            opition_plan=option_obj
            , file=file
            , sheet_name=sheet_name
            , period=period_obj
            , year=year
        )
        plan.save()

        PercentSubdivisionSeason.create_by_file(plan)

    form = PercentageSubdivisionForm()
    return render(
        request
        , "PlanningSystem/load_percentage_subdivision.html", {
            'form': form
        , 'templates_file':templates_file
    }
    )