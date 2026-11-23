"""Виртуальная память: страницы, ограниченное число физических кадров,
страничные прерывания и замена страниц.

Сегмент делится на страницы по page_size символов. Таблица страниц
(page_table) отображает (номер сегмента, страница) -> номер кадра; отсутствие
записи означает, что страница лежит только на диске (backing store).
У каждого кадра есть биты used (обращались) и modified (изменялась), а также
время загрузки и последнего обращения (для FIFO и LRU).

Алгоритмы выбора жертвы:
  clock — «часы» (второй шанс), как в Multics: стрелка обходит кадры по кругу,
          кадр с used=1 получает второй шанс (бит сбрасывается);
  fifo  — вытесняется страница, загруженная раньше всех;
  lru   — вытесняется страница, к которой дольше всех не обращались.
"""
from dataclasses import dataclass
from math import ceil

from .errors import BadArgument

POLICIES = {"clock": "часы (второй шанс)", "fifo": "FIFO", "lru": "LRU"}


@dataclass
class Frame:
    index: int
    segno: int = None
    page: int = None
    used: bool = False       # бит обращения
    modified: bool = False   # бит модификации
    loaded_at: int = 0       # момент загрузки (для FIFO)
    last_used: int = 0       # момент последнего обращения (для LRU)

    @property
    def free(self):
        return self.segno is None

    def clear(self):
        self.segno = self.page = None
        self.used = self.modified = False


class VirtualMemory:
    def __init__(self, n_frames, page_size, out, policy="clock"):
        self.page_size = page_size
        self.out = out
        self.policy = policy
        self.segs = {}            # segno -> Segment (для имён в сообщениях)
        self._init_frames(n_frames)
        self.reset_stats()

    def _init_frames(self, n):
        self.frames = [Frame(i) for i in range(n)]
        self.page_table = {}      # (segno, page) -> индекс кадра
        self.hand = 0             # стрелка часов
        self.time = 0             # счётчик обращений (логическое время)

    def reset_stats(self):
        self.accesses = self.hits = self.faults = self.evictions = self.writebacks = 0

    def _name(self, segno):
        seg = self.segs.get(segno)
        return seg.name if seg else "?"

    def pages_of(self, seg):
        """Сколько страниц занимает сегмент (минимум одна)."""
        return max(1, ceil(seg.size / self.page_size))

    def resident_pages(self, segno):
        return sorted(p for (s, p) in self.page_table if s == segno)

    # ------------------------------------------------------------- обращение
    def access(self, seg, offset, write, proc=None):
        """Обращение к байту offset сегмента. Возвращает True при попадании.
        При отсутствии страницы в памяти возникает страничное прерывание,
        которое обрабатывает ядро (кольцо 0)."""
        self.segs[seg.segno] = seg
        self.time += 1
        page = offset // self.page_size
        key = (seg.segno, page)
        self.accesses += 1
        idx = self.page_table.get(key)
        if idx is not None:                                  # страница в памяти
            frame = self.frames[idx]
            frame.used = True
            frame.modified |= write
            frame.last_used = self.time
            self.hits += 1
            return True
        self.faults += 1                                     # страничное прерывание
        if proc is not None:
            proc.page_faults += 1
        self.out(f"  [ядро, кольцо 0] страничное прерывание: сегмент #{seg.segno} ({seg.name}), "
                 f"страница {page}{' (запись)' if write else ''}")
        idx = self._obtain_frame()
        frame = self.frames[idx]
        frame.segno, frame.page, frame.used, frame.modified = seg.segno, page, True, write
        frame.loaded_at = frame.last_used = self.time
        self.page_table[key] = idx
        self.out(f"      страница загружена с диска в кадр {idx}")
        return False

    def _obtain_frame(self):
        """Свободный кадр или жертва по выбранному алгоритму."""
        for f in self.frames:
            if f.free:
                return f.index
        if self.policy == "fifo":
            victim = min(self.frames, key=lambda f: f.loaded_at).index
            how = "FIFO: загружена раньше всех"
        elif self.policy == "lru":
            victim = min(self.frames, key=lambda f: f.last_used).index
            how = "LRU: дольше всех без обращений"
        else:
            victim, skipped = self._clock_victim()
            how = f"часы: пропущено кадров со вторым шансом: {skipped}"
        self._evict(victim, how)
        return victim

    def _clock_victim(self):
        skipped = 0
        while True:
            f = self.frames[self.hand]
            self.hand = (self.hand + 1) % len(self.frames)
            if f.used:                       # второй шанс: сбросить бит и идти дальше
                f.used = False
                skipped += 1
                continue
            return f.index, skipped

    def _evict(self, idx, how):
        f = self.frames[idx]
        msg = (f"      {how}; вытесняется страница {f.page} сегмента #{f.segno} "
               f"({self._name(f.segno)}) из кадра {idx}")
        if f.modified:
            self.writebacks += 1
            msg += " — страница изменена, запись на диск"
        self.out(msg)
        self.evictions += 1
        del self.page_table[(f.segno, f.page)]
        f.clear()

    def drop_segment(self, segno, from_page=0):
        """Освободить кадры сегмента (удаление файла или усечение содержимого)."""
        for (s, p), idx in list(self.page_table.items()):
            if s == segno and p >= from_page:
                self.frames[idx].clear()
                del self.page_table[(s, p)]

    # ------------------------------------------------------- реконфигурация
    def set_policy(self, name):
        if name not in POLICIES:
            raise BadArgument(f"Неизвестный алгоритм «{name}»; доступны: {', '.join(POLICIES)}")
        self.policy = name

    def set_frames(self, n):
        """Изменить объём физической памяти. Все страницы выгружаются на диск."""
        if not 1 <= n <= 64:
            raise BadArgument("Число кадров должно быть от 1 до 64")
        dirty = sum(1 for f in self.frames if not f.free and f.modified)
        self.writebacks += dirty
        self._init_frames(n)
        return dirty

    def page_map(self, seg):
        """[(страница, кадр или None)] — где находится каждая страница сегмента."""
        return [(p, self.page_table.get((seg.segno, p))) for p in range(self.pages_of(seg))]

    # ------------------------------------------------------------ статистика
    def report(self):
        o = self.out
        o(f"Физическая память: {len(self.frames)} кадров по {self.page_size} символов; "
          f"алгоритм замены: {POLICIES[self.policy]}; стрелка часов -> кадр {self.hand}")
        o("  кадр  сегмент                  стр.  used  mod  загр.  обращ.")
        for f in self.frames:
            mark = "  <- стрелка" if f.index == self.hand and self.policy == "clock" else ""
            if f.free:
                o(f"  {f.index:>3}   (свободен){mark}")
            else:
                name = f"#{f.segno} {self._name(f.segno)}"
                o(f"  {f.index:>3}   {name:<22} {f.page:>4}   {int(f.used)}     {int(f.modified)}  "
                  f"{f.loaded_at:>5}  {f.last_used:>6}{mark}")
        rate = f"{100 * self.hits / self.accesses:.0f}%" if self.accesses else "-"
        o(f"Обращений: {self.accesses}, попаданий: {self.hits} ({rate}), страничных прерываний: {self.faults}, "
          f"вытеснений: {self.evictions}, записей на диск: {self.writebacks}")
