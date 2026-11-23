"""Проверка доступа: ACL И кольцо должны одновременно разрешать операцию."""
from dataclasses import dataclass

from .acl import Principal, SEG_MODES, DIR_MODES
from .errors import AccessViolation, BadArgument
from .segment import Segment


@dataclass
class Subject:
    """Исполнитель запроса: пользователь + кольцо, в котором он сейчас работает."""
    principal: Principal
    ring: int

    @property
    def is_kernel(self):
        return self.ring == 0 and self.principal.person == "Initializer"


def ring_decision(seg: Segment, mode: str, ring: int):
    """Только проверка колец. Возвращает (разрешено, кольцо выполнения, пояснение).

    чтение / просмотр (r, s)      : кольцо <= r2
    запись / изменение (w, m, a)  : кольцо <= r1
    исполнение (e)                : кольцо <= r2 -> работает в max(кольцо, r1);
                                    r2 < кольцо <= r3 -> только через шлюз, в кольце r1
    """
    b = seg.brackets
    if mode in "rs":
        if ring <= b.r2:
            return True, ring, f"кольцо {ring} <= r2={b.r2}"
        return False, None, f"чтение/просмотр {seg.path} требует кольцо <= {b.r2}, вы в кольце {ring} (скобки {b})"
    if mode in "wma":
        if ring <= b.r1:
            return True, ring, f"кольцо {ring} <= r1={b.r1}"
        return False, None, f"изменение {seg.path} требует кольцо <= {b.r1}, вы в кольце {ring} (скобки {b})"
    if ring <= b.r2:
        return True, max(ring, b.r1), f"исполнение в кольце {max(ring, b.r1)}"
    if ring <= b.r3:
        if seg.gate:
            return True, b.r1, f"вызов через шлюз, переход в кольцо {b.r1}"
        return False, None, (f"{seg.path} вызывается из колец {b.r2 + 1}..{b.r3} только через шлюз, "
                             f"а он не является шлюзом")
    return False, None, f"{seg.path} недоступен из кольца {ring} (скобки {b})"


def check_access(subject: Subject, seg: Segment, mode: str) -> int:
    """Бросает AccessViolation, если доступ запрещён. Возвращает кольцо,
    в котором будет выполняться операция (важно для режима 'e' и шлюзов)."""
    valid = DIR_MODES if seg.is_dir else SEG_MODES
    if mode not in valid:
        raise BadArgument(f"Режим '{mode}' неприменим к {'каталогу' if seg.is_dir else 'сегменту'}")
    if subject.is_kernel:                       # супервизор обходит проверки
        return subject.ring

    # Шаг 1. ACL
    granted = seg.acl.lookup(subject.principal)
    if mode not in granted:
        raise AccessViolation(
            f"Отказ по ACL: у {subject.principal} нет режима '{mode}' к {seg.path} "
            f"(ACL разрешает: {granted or 'null'})")

    # Шаг 2. Кольцевые скобки
    ok, ring, why = ring_decision(seg, mode, subject.ring)
    if not ok:
        raise AccessViolation(f"Отказ по кольцу: {why}")
    return ring
