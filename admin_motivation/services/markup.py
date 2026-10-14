def motivation_markup(amount, ddp, motivation):
    if amount is None or ddp is None or motivation is None or ddp + motivation == 0:
        return None
    return amount / (ddp + motivation)
