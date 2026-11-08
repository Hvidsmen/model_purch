"""The current Split workspace contains household air conditioners only."""
from hashlib import sha256
from django.db.models.functions import Trim
from .models import SalesPlanningGoods, SalesPlanningGroup

PRODUCT_GROUP = 'Бытовые кондиционеры'


def split_goods(include_inactive=False):
    goods = SalesPlanningGoods.objects.annotate(split_product_group=Trim('product_group')).filter(split_product_group=PRODUCT_GROUP)
    return goods if include_inactive else goods.filter(is_active=True)


def split_groups(queryset=None):
    names = split_goods(include_inactive=True).order_by().values_list('planning_group', flat=True).distinct()
    keys = [sha256(name.strip().casefold().encode('utf-8')).hexdigest() for name in names if name.strip()]
    return (SalesPlanningGroup.objects.all() if queryset is None else queryset).filter(source_key__in=keys)
