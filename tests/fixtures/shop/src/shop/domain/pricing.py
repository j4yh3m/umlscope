from shop.adapters.db import SqlRepository  # deliberate dependency-rule violation


def discount(total, vip):
    return total * 0.9 if vip else total
