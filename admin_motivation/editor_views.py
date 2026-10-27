"""Read-only preflight for the actual unsaved table, never a draft commit endpoint."""
import json
import re
from urllib.parse import quote
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from base.access import motivation_editor_required
from .models import GlobalCoeffVersion, Subdivision, DirectorEmployee, SalesPlanScenario, VariationCalculate, GlobalDirectorCoeff, SubdivisionDirectorCoeff, Goods
from .services.save_verification import SaveVerification
from .services.editor_patch import formulas
from .services import coefficient_coverage


@require_POST
def check(request):
    motivation_editor_required(request)
    try:
        body=json.loads(request.body);data=body['fields']
        if not isinstance(data,dict) or len(data)>50000 or any(not isinstance(key,str) or len(key)>255 or not isinstance(value,(str,int,float)) or len(str(value))>255 for key,value in data.items()):raise ValidationError('Некорректные поля редактора.')
        if any(not re.fullmatch(r'(?:version|revision|employee|(?:global_coeff|sub_coeff|formula_coeff|director_formula|delete_coeff|select_var_calc|original_var_calc|kind_coeff)=\d+|director_coeff=\d+=[0-4])',key) for key in data):raise ValidationError('Неизвестное поле редактора.')
        data={key:str(value) for key,value in data.items()}
        version=get_object_or_404(GlobalCoeffVersion,pk=data['version'])
        if not version.is_editable or str(version.revision)!=str(data.get('revision')):raise ValidationError('Черновик изменился или закрыт для правок. Обновите страницу.')
        sub=get_object_or_404(Subdivision,pk=body['subdivision']) if body.get('subdivision') else None
        employee=get_object_or_404(DirectorEmployee,pk=data['employee'],is_archived=False) if data.get('employee') else None
        keys=[key for key in data if key.startswith(('global_coeff=','sub_coeff=','director_coeff=','formula_coeff=','director_formula=','select_var_calc=','delete_coeff=','kind_coeff='))]
        verification=SaveVerification(version,data,sub,employee) if keys else None
        core,directors=formulas(version,data,sub,employee)
        labels=dict(VariationCalculate.objects.values_list('pk','variation_name'))
        deleted={int(key.split('=')[1]) for key in data if key.startswith('delete_coeff=')}
        patch={'subdivision':sub.subdivision_key if sub else '', 'employee':employee.pk if employee else 0,'core':{key:labels[value] for key,value in core.items()},'directors':{key:labels.get(value) for key,value in directors.items()},'deleted':deleted,'new_directors':[]}
        dir_model=SubdivisionDirectorCoeff if sub else GlobalDirectorCoeff
        scope={'version':version,**({'subdivision':sub,'employee':employee} if sub else {})}
        present=set(dir_model.objects.filter(**scope).values_list('goods_id',flat=True))
        from .views import coefficient_number
        new={int(key.split('=')[1]) for key in data if key.startswith('director_coeff=') and coefficient_number(data[key])!=0}-present-deleted
        if new and sub and (not employee or not employee.subdivisions.filter(pk=sub.pk).exists()):raise ValidationError('Выберите директора подразделения.')
        for good in Goods.objects.filter(pk__in=new):
            patch['new_directors'].append((version.pk,sub.subdivision_key if sub else '',good.planning_group_sales,good.group,good.brand,employee.pk if sub else 0,str(employee) if sub else '',patch['directors'].get(good.pk),good.pk))
        scope_name='subdivision' if sub else 'global'
        coverage_url=reverse('motivation_coefficient_coverage')+f'?version={version.pk}&scope={scope_name}'
        if sub:coverage_url+='&subdivision='+quote(sub.subdivision_key)
        # Validate saved values too; a clean table must not conceal invalid database rates.
        from .services.comparison import coefficient_input
        from .models import GlobalCoeff,SubdivisionCoeff
        current=(SubdivisionCoeff.objects.filter(version=version,subdivision=sub) if sub else GlobalCoeff.objects.filter(version=version))
        prefix='sub_coeff=' if sub else 'global_coeff='
        for pk,good,value in current.values_list('pk','goods_id','motivation_coeff'):
            if good not in deleted:coefficient_input(data.get(prefix+str(pk),value))
        for row in dir_model.objects.filter(**scope).values('goods_id','k0','k1','k2','k3','k4'):
            if row['goods_id'] in deleted:continue
            for index in range(5):coefficient_input(data.get(f'director_coeff={row["goods_id"]}={index}',row[f'k{index}']))
        coverage=None
        if body.get('plan'):
            plan=get_object_or_404(SalesPlanScenario,pk=body['plan'],is_deleted=False)
            result=coefficient_coverage.check(version,coefficient_coverage.plan_rows(plan,sub.subdivision_key if sub else ''),scope=scope_name,patch=patch)
            coverage={key:result[key] for key in ('examined','missing','formula_mismatches','fallback_formula')}
            coverage['issues']=result['issues'][:100]
            coverage_url+=f'&source=plan&plan={plan.pk}&run=1'
        version.refresh_from_db()
        if str(version.revision)!=str(data['revision']):raise ValidationError('Данные изменились во время проверки. Повторите её.')
        return JsonResponse({'changed':verification.changed if verification else 0,'summary':verification.summary() if verification else 'Нет несохранённых правок','coverage':coverage,'coverage_url':coverage_url,'save_first':bool(keys),'scope':str(sub) if sub else 'Глобально'})
    except ValidationError as error:return JsonResponse({'error':' '.join(error.messages)},status=400)
    except (ValueError,TypeError,KeyError,json.JSONDecodeError):return JsonResponse({'error':'Проверьте поля и выбранный план.'},status=400)
