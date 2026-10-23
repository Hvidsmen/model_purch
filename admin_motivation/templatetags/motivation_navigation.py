from django import template
from ..models import Subdivision

register = template.Library()


@register.simple_tag
def motivation_subdivisions():
    # Navigation must not depend on the current view's form/table context.
    return Subdivision.objects.order_by('subdivision_name', 'pk')
