"""Display annual catch-up only with a selected annual period; never change pay."""

def has_year(periods):
    return any(str(p).strip().casefold() in ('5','год','годовая премия') for p in periods)


def columns(headers,show):
    return tuple(i for i,label in enumerate(headers) if show or 'доплат' not in str(label).casefold())
