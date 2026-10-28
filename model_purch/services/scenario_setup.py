"""Session-scoped recommendations, not a gate or a record of completed work."""
KEY = 'model_purch_setup_scenarios'


def begin_setup(request, scenario):
    pending = request.session.get(KEY, [])
    request.session[KEY] = list(dict.fromkeys([*pending, scenario.pk]))[-100:]


def show_setup(request, scenario):
    if scenario is None:
        return False
    if request.GET.get('setup') == '1':
        begin_setup(request, scenario)
    return scenario.pk in request.session.get(KEY, [])


def finish_setup(request, scenario):
    request.session[KEY] = [pk for pk in request.session.get(KEY, []) if pk != scenario.pk]
