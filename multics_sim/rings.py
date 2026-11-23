"""Кольца защиты и кольцевые скобки сегмента.

Кольца 0..7: чем МЕНЬШЕ номер, тем БОЛЬШЕ привилегий. Ядро — кольцо 0,
пользователь — кольцо 4. У сегмента три числа r1 <= r2 <= r3:

  запись            : кольцо <= r1
  чтение            : кольцо <= r2
  исполнение        : кольцо <= r2 (процедура работает в кольце max(кольцо, r1))
  вызов шлюза (gate): r2 < кольцо <= r3, только если сегмент — шлюз;
                      выполнение при этом переходит в кольцо r1
"""
from dataclasses import dataclass

from .errors import BadArgument

KERNEL_RING = 0
USER_RING = 4
MAX_RING = 7


@dataclass
class RingBrackets:
    r1: int
    r2: int
    r3: int

    def __post_init__(self):
        if not (0 <= self.r1 <= self.r2 <= self.r3 <= MAX_RING):
            raise BadArgument(f"Кольцевые скобки {self.r1},{self.r2},{self.r3} недопустимы: "
                              f"нужно 0 <= r1 <= r2 <= r3 <= {MAX_RING}")

    def __str__(self):
        return f"{self.r1},{self.r2},{self.r3}"
