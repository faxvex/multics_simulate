"""Команды виртуальной памяти: статистика, алгоритмы замены, ручные обращения."""
from ..access import check_access, ring_decision
from ..errors import AccessViolation, BadArgument, to_int
from ..memory import POLICIES
from .base import G_MEM, command, need


class MemoryCommands:
    @command("memstat", "ms", usage="memstat [-reset]", group=G_MEM,
             help="Кадры, страничные прерывания, алгоритм замены; -reset обнуляет счётчики.")
    def cmd_memstat(self, a):
        if "-reset" in a:
            self.system.memory.reset_stats()
            self.out("Счётчики памяти обнулены.")
            return
        self.system.memory.report()

    @command("set_policy", "spol", usage="set_policy clock|fifo|lru", group=G_MEM,
             help="Алгоритм замены страниц: clock (часы, как в Multics), fifo или lru.")
    def cmd_set_policy(self, a):
        need(a, 1, "set_policy clock|fifo|lru")
        self.system.memory.set_policy(a[0])
        self.out(f"Алгоритм замены страниц: {POLICIES[a[0]]}")

    @command("set_frames", "sfr", usage="set_frames N", group=G_MEM,
             help="Число физических кадров (1..64). Все страницы при этом выгружаются на диск.")
    def cmd_set_frames(self, a):
        need(a, 1, "set_frames N")
        dirty = self.system.memory.set_frames(to_int(a[0], "число кадров", 1))
        self.out(f"Физическая память: {a[0]} кадров; выгружено изменённых страниц: {dirty}")

    @command("page_table", "pt", usage="page_table путь", group=G_MEM,
             help="Где находится каждая страница сегмента: в кадре или на диске.")
    def cmd_page_table(self, a):
        need(a, 1, "page_table путь")
        seg = self._resolve(a[0])
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог, у него нет страниц")
        # как и status: нужен режим s на содержащем каталоге (и кольцо, разрешающее просмотр)
        check_access(self.proc.subject, self.system.fs.controlling_dir(seg), "s")
        ok, _, why = ring_decision(seg, "r", self.proc.ring)
        if not ok:
            raise AccessViolation(f"Отказ по кольцу: {why}")
        mem = self.system.memory
        self.out(f"Таблица страниц сегмента #{seg.segno} ({seg.path}), {seg.size} симв.:")
        for page, frame in mem.page_map(seg):
            if frame is None:
                self.out(f"  стр. {page}: на диске")
            else:
                f = mem.frames[frame]
                self.out(f"  стр. {page}: кадр {frame} (used={int(f.used)}, modified={int(f.modified)})")

    @command("touch", usage="touch путь смещение [-w]", group=G_MEM,
             help="Обратиться к одному байту сегмента (чтение или запись -w) и увидеть попадание/прерывание.")
    def cmd_touch(self, a):
        need(a, 2, "touch путь смещение [-w]")
        seg = self._resolve(a[0])
        offset = to_int(a[1], "смещение")
        mem = self.system.memory
        before = mem.faults
        self.system.reference(self.proc, seg, "w" if "-w" in a else "r", offset)
        if mem.faults == before:
            frame = mem.page_table[(seg.segno, offset // mem.page_size)]
            self.out(f"Попадание: страница {offset // mem.page_size} уже в кадре {frame}")
