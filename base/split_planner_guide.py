from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .sales_planning_access import require

@login_required
@require_GET
def guide(request):
    require(request.user)
    return render(request,'base/split_planner_guide.html')
