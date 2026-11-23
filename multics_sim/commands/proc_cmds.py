"""Процессы, KST, динамическое связывание, правила поиска, планировщик, печать."""
from ..access import check_access
from ..errors import AccessViolation, BadArgument, MulticsError, NotFound, to_int
from ..process import Task, WDIR
from .base import G_PROC, command, need

DEFAULT_RULES = [WDIR, ">sss"]


class ProcessCommands:
    # ---------------------------------------------------------- процессы
    @command("ps", group=G_PROC, help="Таблица процессов (traffic controller).")
    def cmd_ps(self, a):
        o = self.out
        o("  PID  Пользователь               Кольцо  Состояние  Задач  CPU  Стр.прер.  Каталог")
        for p in self.system.processes.values():
            o(f"  {p.pid:>3}  {str(p.principal):<26} {p.ring:>5}   {p.state:<9} {len(p.tasks):>5} "
              f"{p.cpu_ticks:>4} {p.page_faults:>9}  {p.wdir.path}")
        sch = self.system.scheduler
        o(f"  Виртуальное время: {sch.clock}, квант = {sch.QUANTUM} такта, в очереди: {list(sch.queue) or '-'}")

    @command("resource_usage", "ru", group=G_PROC, help="Ресурсы, израсходованные текущим процессом.")
    def cmd_resource_usage(self, a):
        p = self.proc
        self.out(f"Процесс {p.pid} ({p.principal}), вход в {p.login_time:%H:%M:%S}")
        self.out(f"  процессорное время : {p.cpu_ticks} тактов")
        self.out(f"  страничных прерываний: {p.page_faults}")
        self.out(f"  известных сегментов (KST): {len(p.kst.entries)}, связей: {len(p.linkage)}")
        self.out(f"  задач в очереди    : {len(p.tasks)}")

    @command("run", usage="run программа", group=G_PROC, help="Выполнить исполняемый сегмент сразу (в текущем процессе).")
    def cmd_run(self, a):
        need(a, 1, "run программа")
        seg = self._find_program(a[0])
        for _ in self.system.interp.run_segment(self.proc, seg):
            self.proc.cpu_ticks += 1
            self.system.scheduler.clock += 1

    @command("start", usage="start программа", group=G_PROC,
             help="Поставить программу в очередь планировщика как фоновую задачу процесса.")
    def cmd_start(self, a):
        need(a, 1, "start программа")
        seg = self._find_program(a[0])
        if not seg.executable:
            raise BadArgument(f"{seg.path} не является исполняемым сегментом")
        check_access(self.proc.subject, seg, "e")       # отказ сразу, а не при планировании
        self.proc.tasks.append(Task(seg.name, self.system.interp.run_segment(self.proc, seg), self.proc.ring))
        self.system.scheduler.enqueue(self.proc)
        self.out(f"Задача «{seg.name}» поставлена в очередь процесса {self.proc.pid}")

    @command("schedule", "tick", usage="schedule [число квантов]", group=G_PROC,
             help="Запустить traffic controller: round-robin по готовым процессам.")
    def cmd_schedule(self, a):
        self.system.scheduler.run(to_int(a[0], "число квантов", 1) if a else None)

    @command("set_quantum", "sq", usage="set_quantum N", group=G_PROC, help="Длина кванта планировщика (1..20 тактов).")
    def cmd_set_quantum(self, a):
        need(a, 1, "set_quantum N")
        n = to_int(a[0], "длина кванта", 1)
        if n > 20:
            raise BadArgument("Квант должен быть от 1 до 20")
        self.system.scheduler.QUANTUM = n
        self.out(f"Квант планировщика: {n} такта")

    # --------------------------------------------------------- KST и связи
    @command("list_ref_names", "lrn", "kst", group=G_PROC, help="Таблица известных сегментов (KST) процесса.")
    def cmd_kst(self, a):
        p = self.proc
        self.out(f"KST процесса {p.pid} ({p.principal}):")
        self.out("  сегм.  кольцо инициации  путь")
        for e in p.kst.entries.values():
            self.out(f"  #{e.segno:<5} {e.ring:>8}          {e.segment.path}")
        if not p.kst.entries:
            self.out("  (пусто)")

    @command("initiate", "in", usage="initiate путь", group=G_PROC,
             help="Сделать сегмент известным процессу (внести в KST) без обращения к нему.")
    def cmd_initiate(self, a):
        need(a, 1, "initiate путь")
        seg = self._resolve(a[0])
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог")
        last = None
        for m in "rew":
            try:
                check_access(self.proc.subject, seg, m)
                break
            except AccessViolation as e:
                last = e
        else:
            raise last
        new = self.proc.kst.initiate(seg, self.proc.ring)
        self.out(f"Сегмент #{seg.segno} ({seg.path}) {'внесён в KST' if new else 'уже был известен'}")

    @command("terminate", "tm", usage="terminate путь|-all", group=G_PROC,
             help="Исключить сегмент из KST и разорвать ссылки на него.")
    def cmd_terminate(self, a):
        need(a, 1, "terminate путь|-all")
        p = self.proc
        if a[0] == "-all":
            n = len(p.kst.entries)
            p.kst.entries.clear()
            p.linkage.clear()
            self.out(f"Исключено сегментов: {n}; все ссылки разорваны")
            return
        seg = self._resolve(a[0])
        if seg.segno not in p.kst.entries:
            raise NotFound(f"{seg.path} не известен процессу")
        p.kst.terminate(seg.segno)
        dropped = [k for k, link in p.linkage.items() if link.segment is seg]
        for k in dropped:
            del p.linkage[k]
        self.out(f"Сегмент #{seg.segno} исключён из KST, разорвано ссылок: {len(dropped)}")

    @command("print_linkage", "plk", "links", group=G_PROC, help="Таблица связей процесса: какие ссылки защёлкнуты.")
    def cmd_links(self, a):
        p = self.proc
        self.out(f"Связи процесса {p.pid}:")
        for (segno, name), link in p.linkage.items():
            caller = next((e.segment.name for e in p.kst.entries.values() if e.segno == segno), f"#{segno}")
            state = f"защёлкнута -> {link.segment.path}" if link.snapped else "не разрешена"
            self.out(f"  {caller} -> {name}: {state}")
        if not p.linkage:
            self.out("  (нет ссылок)")

    @command("reset_links", "rl", group=G_PROC, help="Сбросить все ссылки процесса (снова будет linkage fault).")
    def cmd_reset_links(self, a):
        n = len(self.proc.linkage)
        self.proc.linkage.clear()
        self.out(f"Сброшено ссылок: {n}")

    # ------------------------------------------------------ правила поиска
    def _rule_label(self, rule):
        return f"рабочий каталог ({self.proc.wdir.path})" if rule == WDIR else rule

    @command("print_search_rules", "psr", "search_rules", "sr", group=G_PROC, help="Показать правила поиска процедур.")
    def cmd_search_rules(self, a):
        for i, r in enumerate(self.proc.search_rules, 1):
            self.out(f"  {i}. {self._rule_label(r)}")

    def _parse_rule(self, text):
        if text in ("working_dir", "wd", "-wd"):
            return WDIR
        d = self._resolve(text)
        if not d.is_dir:
            raise BadArgument(f"{d.path} — не каталог")
        return d.path

    @command("add_search_rules", "asr", usage="add_search_rules каталог|working_dir [-first]", group=G_PROC,
             help="Добавить правило поиска (в конец или, с -first, в начало).")
    def cmd_add_search_rules(self, a):
        need(a, 1, "add_search_rules каталог [-first]")
        rule = self._parse_rule(a[0])
        rules = self.proc.search_rules
        if rule in rules:
            rules.remove(rule)
        rules.insert(0, rule) if "-first" in a else rules.append(rule)
        self.cmd_search_rules([])

    @command("delete_search_rules", "dsr", usage="delete_search_rules каталог|working_dir", group=G_PROC,
             help="Удалить правило поиска.")
    def cmd_delete_search_rules(self, a):
        need(a, 1, "delete_search_rules каталог")
        rule = WDIR if a[0] in ("working_dir", "wd", "-wd") else self._resolve(a[0]).path
        if rule not in self.proc.search_rules:
            raise NotFound(f"Правила {a[0]} нет")
        self.proc.search_rules.remove(rule)
        self.cmd_search_rules([])

    @command("set_search_rules", "ssr", usage="set_search_rules default", group=G_PROC,
             help="Вернуть правила поиска по умолчанию (рабочий каталог, затем >sss).")
    def cmd_set_search_rules(self, a):
        self.proc.search_rules = list(DEFAULT_RULES)
        self.cmd_search_rules([])

    @command("where", "wh", usage="where имя", group=G_PROC,
             help="Где будет найдена процедура по правилам поиска (и где её ещё можно найти).")
    def cmd_where(self, a):
        need(a, 1, "where имя")
        found = False
        for i, rule in enumerate(self.proc.search_rules, 1):
            try:
                d = self.system.linker.rule_dir(self.proc, rule)
            except NotFound:
                self.out(f"  {i}. {rule}: каталог не существует")
                continue
            seg = d.children.get(a[0])
            if seg is None or seg.is_dir:
                self.out(f"  {i}. {d.path}: нет")
                continue
            mark = "  <- будет использован" if not found else "  (перекрыт)"
            found = True
            self.out(f"  {i}. {self.system.fs.join(d, a[0])} (#{seg.segno}){mark}")
        if not found:
            self.out(f"«{a[0]}» не найден по правилам поиска")

    # -------------------------------------------------------- демон печати
    @command("dprint", "dp", usage="dprint путь [-copy N]", group=G_PROC,
             help="Поставить сегмент в очередь печати; печатает демон IO.SysDaemon при schedule.")
    def cmd_dprint(self, a):
        need(a, 1, "dprint путь [-copy N]")
        copies = 1
        if "-copy" in a:
            i = a.index("-copy")
            need(a, i + 2, "dprint путь -copy N")
            copies = to_int(a[i + 1], "число копий", 1)
        ids = self.system.dprint(self.proc, self._resolve(a[0]), min(copies, 5))
        self.out(f"Запросы печати: {', '.join('№' + str(i) for i in ids)}; в очереди: "
                 f"{len(self.system.print_queue)} (выполнятся при schedule)")

    @command("list_daemon_requests", "ldr", group=G_PROC, help="Очередь демона печати.")
    def cmd_ldr(self, a):
        q = self.system.print_queue
        if not q:
            self.out("Очередь печати пуста.")
        for r in q:
            self.out(f"  №{r.id}  {r.path}  ({r.user})")

    @command("cancel_daemon_request", "cdr", usage="cancel_daemon_request номер", group=G_PROC,
             help="Отменить свой запрос печати.")
    def cmd_cdr(self, a):
        need(a, 1, "cancel_daemon_request номер")
        req = self.system.cancel_print(self.proc, to_int(a[0], "номер запроса", 1))
        self.out(f"Запрос №{req.id} ({req.path}) отменён")
