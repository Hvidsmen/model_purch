"""Optional Dash applications, registered only when explicitly enabled."""


def register_apps():
    from . import stock_chart  # noqa: F401
