from django import template
from ..models import Subdivision

register = template.Library()


@register.simple_tag
def motivation_subdivisions():
    # Navigation must not depend on the current view's form/table context.
    return Subdivision.objects.order_by('subdivision_name', 'pk')


@register.simple_tag(takes_context=True)
def motivation_workflow(context):
    from ..services.workflow import context as state
    from ..models import GlobalCoeffVersion
    result=state()
    request=context.get('request')
    selected=context.get('selected_version')
    result['selected']=selected if isinstance(selected,GlobalCoeffVersion) else GlobalCoeffVersion.objects.filter(pk=request.session.get('motivation_version')).first() if request else None
    from base.access import role_for, MOTIVATION_VIEWER, MODELER, FINANCIER, DIRECTOR
    if request and role_for(request.user, 'motivation')==MOTIVATION_VIEWER:
        from django.urls import reverse
        result['next_label']='Открыть отчёты'
        result['next_url']=reverse('motivation_reports')
    elif result['selected'] and result['selected'].is_editable:
        from django.urls import reverse
        result['next_label']='Проверить и утвердить черновик'
        result['next_url']=reverse('global_coeff_admin_motivation')+'?version='+str(result['selected'].pk)+'#version-approval-actions'
    if request and role_for(request.user, 'motivation') in (MODELER, FINANCIER, DIRECTOR):
        from django.urls import reverse
        result['next_label']='Открыть проверку'
        result['next_url']=reverse('motivation_reviews')
    result['sub']=context.get('sub')
    result['employee']=context.get('employee')
    return result
