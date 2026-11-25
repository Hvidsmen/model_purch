"""Single ordered branch list for navigation and closing-period selection."""
from ..models import Subdivision


def navigation_subdivisions():
    return Subdivision.objects.order_by('subdivision_name','pk')
