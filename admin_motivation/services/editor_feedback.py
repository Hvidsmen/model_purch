import re
from django.contrib import messages


def restore_context(request):
    data = getattr(request,'coefficient_attempt',None)
    if data is None:
        return {}
    allowed = re.compile(r'^(?:(?:global_coeff|sub_coeff|select_var_calc|delete_coeff|kind_coeff|formula_coeff|director_formula)=\d+|director_coeff=\d+=[0-4])$')
    fields = {key:value for key,value in data.items() if allowed.fullmatch(key) or data.get('action_button')=='add_product' and (key in {'pg_sales','group','brand','select_var_calc'} or re.fullmatch(r'[ks]\d+',key))}
    errors = {}
    from ..views import coefficient_number
    for key,value in fields.items():
        if key.startswith(('global_coeff=','sub_coeff=','director_coeff=','kind_coeff=')) or re.fullmatch(r'[ks]\d+',key):
            try: coefficient_number(value)
            except Exception: errors[key]='Введите число от −100% до 100%, например 0,8%.'
    return {'coefficient_attempt':{'fields':fields,'errors':errors,'action':data.get('action_button')}}


def recover(request, renderer, version, subdivision=None):
    request.coefficient_attempt = request.POST.copy()
    query=request.GET.copy();query['version']=str(version.pk)
    if request.POST.get('employee'): query['employee']=request.POST['employee']
    request.GET=query
    messages.warning(request,'Введённые значения восстановлены и ещё не сохранены. Проверьте подсвеченные поля перед повторным сохранением.')
    return renderer(request,subdivision) if subdivision else renderer(request)
