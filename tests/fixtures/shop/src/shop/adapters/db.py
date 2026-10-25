import shop.domain as dom
from shop.domain import Repository


class SqlRepository(Repository):
    def save(self, order):
        pass


class Cached(dom.model.Order):
    pass
