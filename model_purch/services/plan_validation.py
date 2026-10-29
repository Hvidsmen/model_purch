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
            warnings.append(('ddp', f'DDP отличается от плана на {percent:.1f}% (порог {threshold:g}%): сейчас {good.ddp_usd or 0:,.2f}, план {good.plan_ddp_usd:,.2f} USD/шт. Откройте расчёт стоимости.'))
    if good.plan_volume is None:
        missing.append('объём')
    elif not math.isclose(good.volume or 0, good.plan_volume, rel_tol=1e-9, abs_tol=1e-9):
        deviation = abs((good.volume or 0) - good.plan_volume)
        if good.plan_volume == 0:
            warnings.append(('volume', 'Объём отличается от нулевого плана'))
        elif deviation / abs(good.plan_volume) * 100 > threshold + 1e-9:
            percent = deviation / abs(good.plan_volume) * 100
            warnings.append(('volume', f'Объём отличается от плана на {percent:.1f}% (порог {threshold:g}%): сейчас {good.volume or 0:,.2f}, план {good.plan_volume:,.2f} м³/шт. Проверьте объём товара.'))
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
                warnings.append((kind, f'{label} отличается от факта на {percent:.1f}% (порог {threshold:g}%): сейчас {current or 0:,.2f}, факт {actual:,.2f}. Проверьте параметры товара.'))
    if missing:
        warnings.append(('missing', 'Не загружены плановые значения: ' + ', '.join(missing)))
    return warnings


def duty_warnings(good, group_duties):
    """Advisory only: coefficients are stored, percentages are shown."""
    from ..goods_identity import planning_group_key
    rate = float(good.duty_rate or 0)
    warnings = []
    if 0 < rate < 0.01:
        warnings.append(('duty_small', f'Пошлина {rate * 100:g}%. Возможно, имелось в виду 10%? Проверьте ставку.'))
    group_rate = group_duties.get(planning_group_key(good.group_goods or ''))
    if group_rate is not None and not math.isclose(rate, float(group_rate), rel_tol=1e-9, abs_tol=1e-9):
        warnings.append(('duty_group', f'Пошлина товара {rate * 100:g}%, для группы — {float(group_rate) * 100:g}%. Отличие может быть обоснованным.'))
    return warnings
