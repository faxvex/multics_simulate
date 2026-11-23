"""Командная среда: справка, сценарии exec_com, do, сокращения, история, редактор edm."""
import os
from datetime import datetime

from ..errors import BadArgument, MulticsError, NotFound, to_int
from ..exec_com import ExecCom
from ..starname import is_starname, star_match
from .base import G_FS, G_SHELL, command, need

GROUP_ORDER = ["Файловая система", "Доступ: ACL, кольца, защита", "Процессы, связывание, планировщик",
               "Виртуальная память", "Пользователи и связь", "Администрирование", "Командная среда"]
AF_ORDER = ["Строки", "Арифметика", "Логика и условия", "Пути и файлы", "Время, пользователь, переменные"]


class MiscCommands:
    # ------------------------------------------------------------- справка
    @command("help", "h", "?", usage="help [команда|-af|звёздное_имя]", login=False,
             help="Справка: список команд по группам; help -af — активные функции; help *acl* — поиск.")
    def cmd_help(self, a):
        if a and a[0] == "-af":
            return self._help_list(self.actives, AF_ORDER,
                                   "Активные функции (пишутся в [скобках]: echo [plus 2 2]):")
        if a and is_starname(a[0]):
            hits = sorted({m.name for _, m in list(self.commands.values()) + list(self.actives.values())
                           if any(star_match(a[0], n) for n in (m.name,) + tuple(m.aliases))})
            self.out("Найдено: " + (", ".join(hits) if hits else "ничего"))
            return
        if a:
            shown = False
            for registry, kind in ((self.commands, "Команда"), (self.actives, "Активная функция")):
                entry = registry.get(a[0])
                if entry:
                    m = entry[1]
                    self.out(f"{kind}: {m.usage}\n  {m.help}\n  алиасы: {', '.join(m.aliases) or '-'}")
                    shown = True
            if not shown:
                raise NotFound(f"Нет команды или активной функции «{a[0]}» (поиск: help *{a[0]}*)")
            return
        self._help_list(self.commands, GROUP_ORDER, "Команды (в скобках — алиасы). help <имя> — подробно.")
        self.out(f"Активных функций: {len({m.name for _, m in self.actives.values()})} — см. help -af")

    def _help_list(self, registry, order, title):
        self.out(title)
        unique = {m.name: m for _, m in registry.values()}
        groups = {}
        for m in unique.values():
            groups.setdefault(m.group, []).append(m)
        for g in order + sorted(set(groups) - set(order)):
            if g not in groups:
                continue
            self.out(f"\n  == {g} ==")
            for m in sorted(groups[g], key=lambda m: m.name):
                al = f" ({', '.join(m.aliases)})" if m.aliases else ""
                self.out(f"  {m.name + al:<38} {m.help}")
        self.out(f"\nВсего: {len(unique)}")

    # ------------------------------------------------------------ простое
    @command("echo", login=False, help="Напечатать аргументы.")
    def cmd_echo(self, a):
        self.out(" ".join(a))

    @command("clear", "cls", login=False, help="Очистить экран.")
    def cmd_clear(self, a):
        if self.out is print:
            os.system("cls" if os.name == "nt" else "clear")

    @command("demo", login=False, help="Демонстрационный сценарий (в отдельной свежей системе).")
    def cmd_demo(self, a):
        from ..demo import run_demo
        run_demo(self.out)

    @command("exit", "quit", "bye", login=False, help="Выйти из эмулятора.")
    def cmd_exit(self, a):
        self.running = False

    # ---------------------------------------------------- ready-сообщение
    @command("ready_on", "rdn", group=G_SHELL,
             help="После каждой команды печатать ready-сообщение: r ЧЧ:ММ такты_CPU страничные_прерывания.")
    def cmd_ready_on(self, a):
        self.proc.ready = True
        self.proc.ready_mark = (self.proc.cpu_ticks, self.proc.page_faults)

    @command("ready_off", "rdf", group=G_SHELL, help="Не печатать ready-сообщение.")
    def cmd_ready_off(self, a):
        self.proc.ready = False

    @command("ready", "rdy", group=G_SHELL, help="Напечатать ready-сообщение один раз.")
    def cmd_ready(self, a):
        if not self.proc.ready:
            self.print_ready()

    def print_ready(self):
        """Как в Multics: время, расход CPU и страничные прерывания с прошлого ready."""
        p = self.proc
        cpu, faults = p.cpu_ticks - p.ready_mark[0], p.page_faults - p.ready_mark[1]
        p.ready_mark = (p.cpu_ticks, p.page_faults)
        self.out(f"r {datetime.now():%H:%M} {cpu} {faults}")

    # ---------------------------------------------------------- сценарии
    @command("exec_com", "ec", usage="exec_com путь [аргументы...]", group=G_SHELL,
             help="Выполнить сценарий команд (ищется путь.ec, затем путь); &1, &if, &goto, &print...")
    def cmd_exec_com(self, a):
        need(a, 1, "exec_com путь [аргументы]")
        try:
            seg = self._resolve(a[0] + ".ec")
        except MulticsError:
            seg = self._resolve(a[0])
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог")
        self.system.reference(self.proc, seg, "r")
        if self._ec_depth >= 8:
            raise MulticsError("exec_com: слишком глубокая вложенность сценариев")
        self._ec_depth += 1
        try:
            ExecCom(self, seg, a[1:]).run()
        finally:
            self._ec_depth -= 1

    @command("do", usage='do "команда с &1 &2" аргументы...', login=False, group=G_SHELL,
             help="Подставить аргументы в шаблон команды и выполнить: do \"copy &1 &1.bak\" notes")
    def cmd_do(self, a):
        need(a, 1, 'do "шаблон" аргументы')
        fake = type("Seg", (), {"content": ""})()
        self.execute(ExecCom(self, fake, a[1:]).substitute(a[0]))

    @command("abbrev", "ab", usage="abbrev add|list|delete [имя] [текст]", group=G_SHELL,
             help="Сокращения: первое слово команды заменяется текстом (abbrev add ll list -seg).")
    def cmd_abbrev(self, a):
        table = self.system.abbrevs.setdefault(self.system.key(self.proc.principal), {})
        op = a[0] if a else "list"
        if op in ("add", ".a"):
            need(a, 3, "abbrev add имя текст")
            if a[1] in ("abbrev", "ab"):
                raise BadArgument("Нельзя переопределить саму команду abbrev")
            table[a[1]] = " ".join(a[2:])
            self.out(f"{a[1]} -> {table[a[1]]}")
        elif op in ("delete", ".d"):
            need(a, 2, "abbrev delete имя")
            if table.pop(a[1], None) is None:
                raise NotFound(f"Нет сокращения «{a[1]}»")
            self.out(f"Сокращение {a[1]} удалено")
        elif op in ("list", ".l"):
            if not table:
                self.out("Сокращений нет.")
            for k in sorted(table):
                self.out(f"  {k} -> {table[k]}")
        else:
            raise BadArgument("abbrev add|list|delete")

    @command("history", "hist", usage="history [N]", login=False, group=G_SHELL, help="Последние N введённых команд.")
    def cmd_history(self, a):
        n = to_int(a[0], "число", 1) if a else 20
        for i, line in list(enumerate(self.history, 1))[-n:]:
            self.out(f"  {i:>4}  {line}")

    @command("redo", usage="redo [номер]", login=False, group=G_SHELL, help="Повторить команду из истории.")
    def cmd_redo(self, a):
        if not self.history:                            # сам redo в историю не пишется
            raise NotFound("История пуста")
        n = to_int(a[0], "номер", 1) if a else len(self.history)
        if n > len(self.history):
            raise NotFound(f"В истории нет команды №{n}")
        line = self.history[n - 1]
        if "****" in line.split():
            raise BadArgument("Команда содержала пароль (в истории он скрыт) — наберите её заново")
        self.out(f"redo> {line}")
        self.execute(line)

    # ------------------------------------------------------------- редактор
    @command("edm", usage="edm путь", group=G_FS,
             help="Строчный редактор: a — ввод (до строки '.'), p — печать, d N, r N текст, s/старое/новое/, w, q.")
    def cmd_edm(self, a):
        need(a, 1, "edm путь")
        fs = self.system.fs
        try:
            seg = self._resolve(a[0])
            self.system.reference(self.proc, seg, "r")
            lines = seg.content.split("\n") if seg.content else []
        except NotFound:
            seg, lines = None, []
            self.out(f"Новый сегмент {a[0]}.")
        self.out("edm: a — ввод, p — печать, d N [M], i N, r N текст, s/старое/новое/, w — записать, q — выход, h — справка")
        dirty = False
        while True:
            req = self._read_line("edm> ")
            if req is None:
                return
            req = req.strip()
            cmd, _, rest = req.partition(" ")
            try:
                if req in ("a", "i") or cmd == "i":
                    pos = len(lines) if cmd == "a" else to_int(rest or "1", "номер строки", 1) - 1
                    new = []
                    while True:
                        l = self._read_line("")
                        if l is None or l == ".":
                            break
                        new.append(l)
                    lines[pos:pos] = new
                    dirty = dirty or bool(new)
                elif req == "p":
                    for n, l in enumerate(lines, 1):
                        self.out(f"{n:>4}  {l}")
                    if not lines:
                        self.out("(пусто)")
                elif cmd == "d":
                    nums = [to_int(x, "номер строки", 1) for x in rest.split()]
                    if not nums:
                        raise BadArgument("d N [M]")
                    first, last = nums[0], nums[-1]
                    del lines[first - 1:last]
                    dirty = True
                elif cmd == "r":
                    num, _, text = rest.partition(" ")
                    n = to_int(num, "номер строки", 1)
                    if n > len(lines):
                        raise BadArgument(f"Нет строки {n}")
                    lines[n - 1] = text
                    dirty = True
                elif req.startswith("s/"):
                    parts = req.split("/")
                    if len(parts) < 4:
                        raise BadArgument("s/старое/новое/")
                    count = sum(l.count(parts[1]) for l in lines)
                    lines = [l.replace(parts[1], parts[2]) for l in lines]
                    self.out(f"Замен: {count}")
                    dirty = dirty or count > 0
                elif req == "w":
                    if seg is None:
                        seg = fs.create(self.proc.subject, self.proc.wdir, a[0])
                    self.system.set_content(self.proc, seg, "\n".join(lines))
                    self.out(f"Записано {seg.size} символов в {seg.path}")
                    dirty = False
                elif req in ("q", "Q"):
                    if dirty and req == "q":
                        self.out("Есть незаписанные изменения: w — записать, Q — выйти без записи.")
                        continue
                    return
                elif req == "h":
                    self.out("a, i N, p, d N [M], r N текст, s/старое/новое/, w, q, Q")
                elif req:
                    self.out(f"edm: неизвестный запрос «{req}» (h — справка)")
            except MulticsError as e:
                self.out(f"Ошибка: {e}")

    def _read_line(self, prompt):
        try:
            return self.input_fn(prompt)
        except (EOFError, StopIteration):
            return None
