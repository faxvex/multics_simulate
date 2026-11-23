"""Интерпретатор «машинного кода» исполняемых сегментов (мини-скрипт).

Инструкции (по одной в строке, '#' — комментарий):
  echo <текст>          вывести текст от имени процесса
  call <имя|путь>       вызвать другой процедурный сегмент (динамическое связывание)
  read <путь> [смещ.]   прочитать сегмент (целиком или страницу по смещению)
  store <путь> <смещ.>  записать в сегмент по смещению (ставит бит modified)
  compute <N>           занять процессор на N тактов
  ring                  вывести текущее кольцо
  return                выйти из процедуры

Выполнение — генератор: после каждой выполненной инструкции он делает yield,
отдавая управление планировщику (один yield = один такт процессора).
"""
from .errors import BadArgument, MulticsError, to_int

MAX_DEPTH = 8


class Interpreter:
    def __init__(self, system):
        self.system = system

    def run_segment(self, proc, seg, depth=0):
        sysm = self.system
        if depth > MAX_DEPTH:
            raise MulticsError("Переполнение стека вызовов (слишком глубокая рекурсия)")
        if not seg.executable:
            raise MulticsError(f"{seg.path} не является исполняемым сегментом")
        old_ring = proc.ring
        ring = sysm.reference(proc, seg, "e", touch=False)        # ACL + кольцо
        if ring != old_ring:
            what = "вход через шлюз" if seg.gate and old_ring > seg.brackets.r2 else "смена кольца"
            sysm.out(f"  [ядро] {what}: кольцо {old_ring} -> {ring} (сегмент {seg.name})")
        proc.ring = ring
        try:
            offset = 0
            for raw in seg.content.split("\n"):
                line_start, offset = offset, offset + len(raw) + 1
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                sysm.memory.access(seg, line_start, False, proc)  # выборка инструкции
                op, _, arg = line.partition(" ")
                arg = arg.strip()
                if op in ("call", "read", "store") and not arg:
                    raise BadArgument(f"{seg.name}: инструкции {op} нужен аргумент")
                if op == "echo":
                    proc.say(arg)
                elif op == "call":
                    callee, _ = sysm.linker.resolve_call(proc, seg, arg)
                    yield from self.run_segment(proc, callee, depth + 1)
                    continue                     # такты вызова уже учтены вызванной процедурой
                elif op == "read":
                    parts = arg.split()
                    target = sysm.fs.resolve(parts[0], proc.wdir)
                    off = to_int(parts[1], "смещение") if len(parts) > 1 else None
                    sysm.reference(proc, target, "r", off)
                    proc.say(f"прочитан сегмент {target.path}")
                elif op == "store":
                    parts = arg.split()
                    if len(parts) != 2:
                        raise BadArgument("store <путь> <смещение>")
                    target = sysm.fs.resolve(parts[0], proc.wdir)
                    sysm.reference(proc, target, "w", to_int(parts[1], "смещение"))
                    proc.say(f"запись в {target.path}, смещение {parts[1]}")
                elif op == "compute":
                    for _ in range(to_int(arg or "1", "число тактов", 1) - 1):
                        yield                    # N-1 тактов здесь + 1 общий ниже = N
                elif op == "ring":
                    proc.say(f"текущее кольцо = {proc.ring}")
                elif op == "return":
                    return
                else:
                    raise MulticsError(f"{seg.name}: неизвестная инструкция «{op}»")
                yield                            # инструкция выполнена: один такт CPU
        finally:
            if proc.ring != old_ring:
                sysm.out(f"  [ядро] возврат: кольцо {proc.ring} -> {old_ring}")
            proc.ring = old_ring
