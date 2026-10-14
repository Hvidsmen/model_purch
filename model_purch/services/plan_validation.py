"""Advisory checks against imported unit plan parameters."""
import math


def plan_warnings(good, threshold=10):
    warnings = []
    missing = []
    if good.plan_ddp_usd is None:
        missing.append('DDP')
    elif not math.isclose(good.ddp_usd or 0, good.plan_ddp_usd, rel_tol=1e-9, abs_tol=1e-9):
        deviation = abs((good.ddp_usd or 0) - good.plan_ddp_usd)
        if good.plan_ddp_usd == 0:
            warnings.append(('ddp', 'DDP отличается от нулевого плана'))
        elif deviation / abs(good.plan_ddp_usd) * 100 > threshold + 1e-9:
            percent = deviation / abs(good.plan_ddp_usd) * 100
            warnings.append(('ddp', f'DDP отличается от плана на {percent:.1f}% (порог {threshold:g}%)'))
    if good.plan_volume is None:
        missing.append('объём')
    elif not math.isclose(good.volume or 0, good.plan_volume, rel_tol=1e-9, abs_tol=1e-9):
        warnings.append(('volume', f'Объём отличается от плана: {good.volume:g} / {good.plan_volume:g} м³'))
    if missing:
        warnings.append(('missing', 'Не загружены плановые значения: ' + ', '.join(missing)))
    return warnings
