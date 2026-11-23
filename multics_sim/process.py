"""Процесс, его KST (таблица известных сегментов) и таблица связей (linkage)."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .access import Subject
from .acl import Principal
from .segment import Segment

WDIR = "wdir"   # условное имя «рабочий каталог» в правилах поиска


@dataclass
class KnownSegment:
    """Строка KST: сегмент «известен» процессу."""
    segno: int
    segment: Segment
    ring: int   # кольцо процесса в момент инициации


class KST:
    """Known Segment Table — у каждого процесса своя."""

    def __init__(self):
        self.entries = {}

    def initiate(self, seg, ring):
        """Сделать сегмент известным. Возвращает True, если он добавлен впервые."""
        if seg.segno in self.entries:
            return False
        self.entries[seg.segno] = KnownSegment(seg.segno, seg, ring)
        return True

    def terminate(self, segno):
        self.entries.pop(segno, None)


@dataclass
class Link:
    """Связь «вызывающий сегмент -> имя». Пока snapped=False, вызов даёт linkage fault."""
    target_name: str
    snapped: bool = False
    segment: Segment = None


class Task:
    """Задача процесса: генератор, выдающий управление после каждой инструкции.

    ring — кольцо, в котором задача выполняется СЕЙЧАС. Хранится у задачи, а не
    у процесса: если задача приостановлена внутри шлюза (кольцо 0), команды,
    которые пользователь вводит в это время, всё равно идут в его кольце 4.
    """

    def __init__(self, name, gen, ring):
        self.name, self.gen, self.ring = name, gen, ring


class Process:
    def __init__(self, pid, principal: Principal, ring, wdir, out):
        self.pid = pid
        self.principal = principal
        self.ring = ring
        self.wdir = wdir
        self.default_wdir = wdir            # куда ведёт change_wdir без аргументов
        self.pdir = None                    # каталог процесса >pdd>!pid_N
        self.out = out
        self.login_time = datetime.now()
        self.accept_messages = True         # accept_messages / defer_messages
        self.ready = False                  # ready_on / ready_off: печатать ready-сообщение
        self.ready_mark = (0, 0)            # (такты CPU, страничные прерывания) на момент прошлого ready
        self.kst = KST()
        self.linkage = {}                   # (segno вызывающего, имя) -> Link
        self.search_rules = [WDIR, ">sss"]  # правила поиска при linkage fault
        self.tasks = deque()
        self.cpu_ticks = 0
        self.page_faults = 0

    @property
    def subject(self):
        return Subject(self.principal, self.ring)

    @property
    def state(self):
        if self.pid == 0:
            return "система"
        if self.tasks:
            return "готов"
        return "демон" if self.principal.tag == "z" else "ожидает"

    def say(self, text):
        self.out(f"[pid {self.pid} {self.principal.person}] {text}")
