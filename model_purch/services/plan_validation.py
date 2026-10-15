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
        deviation = abs((good.volume or 0) - good.plan_volume)
        if good.plan_volume == 0:
            warnings.append(('volume', 'Объём отличается от нулевого плана'))
        elif deviation / abs(good.plan_volume) * 100 > threshold + 1e-9:
            percent = deviation / abs(good.plan_volume) * 100
            warnings.append(('volume', f'Объём отличается от плана на {percent:.1f}% (порог {threshold:g}%)'))
    for kind, label, current, actual in (
        ('fact_ddp', 'DDP', good.ddp_usd, good.fact_ddp_usd),
        ('fact_volume', 'Объём', good.volume, good.fact_volume),
    ):
        if actual is None or math.isclose(current or 0, actual, rel_tol=1e-9, abs_tol=1e-9):
            continue
        if actual == 0:
            warnings.append((kind, f'{label} отличается от нулевого факта'))
        else:
            percent = abs((current or 0) - actual) / abs(actual) * 100
            if percent > threshold + 1e-9:
                warnings.append((kind, f'{label} отличается от факта на {percent:.1f}% (порог {threshold:g}%)'))
    if missing:
        warnings.append(('missing', 'Не загружены плановые значения: ' + ', '.join(missing)))
    return warnings
