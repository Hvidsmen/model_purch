"""Explicit read routes: a GET may trigger SQL exports or other writes elsewhere."""
READ_ROUTES=frozenset({
    'scenario_list_base','scenario_list','scenario_archive','scenario_edit',
    'pggoods_list','goods_presence','edit_pggoods','export_pggoods','goods_groups','goods_plan_parameters',
    'purch_list','purch_edit','export_purchases','freight','results_page',
    'stock_chart_page','stock_chart_data_api','get_algorithm_statuэ',
})


def read_request(request):
    match=request.resolver_match
    return request.method in ('GET','HEAD','OPTIONS') and bool(match and match.url_name in READ_ROUTES)
