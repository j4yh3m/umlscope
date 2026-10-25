from ..domain import Order
from ..domain.pricing import discount
import importlib


def checkout(lines, vip=False):
    order = Order(lines)
    plugin = importlib.import_module("shop.adapters.db")
    return discount(order.total(), vip)
