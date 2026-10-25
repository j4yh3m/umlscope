from typing import Protocol


class Repository(Protocol):
    def save(self, order: "Order") -> None: ...


class Order:
    def __init__(self, lines):
        self.lines = lines

    def total(self):
        t = 0
        for line in self.lines:
            if line.qty > 0 and line.price > 0:
                t += line.qty * line.price
        return t

    def _audit(self):
        return None

    class Line:
        def cost(self):
            return 1
