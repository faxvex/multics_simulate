"""Команды защиты: ACL, начальные ACL каталогов, кольца и шлюзы, safety switch."""
from ..access import check_access, ring_decision
from ..acl import DIR_MODES, SEG_MODES, parse_ident
from ..errors import BadArgument, to_int
from ..rings import MAX_RING, RingBrackets
from .base import G_ACL, command, need


class AccessCommands:
    def _ident_default(self):
        return f"{self.proc.principal.person}.{self.proc.principal.project}.*"

    def _require_modify(self, seg):
        """Менять атрибуты записи можно при режиме m на содержащем её каталоге."""
        check_access(self.proc.subject, self.system.fs.controlling_dir(seg), "m")

    # --------------------------------------------------------------- ACL
    @command("set_acl", "sa", usage="set_acl путь режимы [Person.Project.tag] [режимы ident]...", group=G_ACL,
             help="Задать записи ACL (rew для сегментов, sma для каталогов, null — нет доступа).")
    def cmd_set_acl(self, a):
        need(a, 2, "set_acl путь режимы [Person.Project.tag]")
        seg = self._resolve(a[0])
        self._require_modify(seg)
        pairs = a[1:] if len(a) > 2 else [a[1], self._ident_default()]
        if len(pairs) % 2:
            raise BadArgument("После пути должны идти пары: режимы идентификатор")
        for modes, ident in zip(pairs[::2], pairs[1::2]):
            seg.acl.set(ident, modes, seg.is_dir)
            full = ".".join(parse_ident(ident))
            e = next(x for x in seg.acl.entries if x.ident == full)
            self.out(f"ACL {seg.path}: {e.ident} -> {self._modes(e.modes)}")

    @command("list_acl", "la", usage="list_acl [путь]", group=G_ACL, help="Показать ACL (режим s в родителе).")
    def cmd_list_acl(self, a):
        seg = self._resolve(a[0]) if a else self.proc.wdir
        check_access(self.proc.subject, self.system.fs.controlling_dir(seg), "s")
        self.out(f"ACL {seg.path}:")
        for e in seg.acl.entries:
            self.out(f"  {self._modes(e.modes):<5} {e.ident}")
        if not seg.acl.entries:
            self.out("  (пусто)")

    @command("delete_acl", "da", usage="delete_acl путь [Person.Project.tag]", group=G_ACL,
             help="Удалить запись ACL (без идентификатора — свою).")
    def cmd_delete_acl(self, a):
        need(a, 1, "delete_acl путь [Person.Project.tag]")
        seg = self._resolve(a[0])
        self._require_modify(seg)
        ident = a[1] if len(a) > 1 else self._ident_default()
        self.out("Запись удалена" if seg.acl.remove(ident) else "Такой записи нет")

    @command("copy_acl", "ca", usage="copy_acl откуда куда", group=G_ACL,
             help="Скопировать ACL одного объекта на другой того же типа.")
    def cmd_copy_acl(self, a):
        need(a, 2, "copy_acl откуда куда")
        src, dst = self._resolve(a[0]), self._resolve(a[1])
        if src.is_dir != dst.is_dir:
            raise BadArgument("ACL можно копировать только между объектами одного типа")
        check_access(self.proc.subject, self.system.fs.controlling_dir(src), "s")
        self._require_modify(dst)
        dst.acl.entries.clear()
        for e in src.acl.entries:
            dst.acl.set(e.ident, e.modes or "null", dst.is_dir)
        self.out(f"ACL {src.path} скопирован на {dst.path} (записей: {len(dst.acl.entries)})")

    # -------------------------------------------------- начальные ACL (IACL)
    @command("set_iacl_seg", "sis", usage="set_iacl_seg каталог режимы [Person.Project.tag]", group=G_ACL,
             help="Начальный ACL: запись, которую получит каждый новый сегмент каталога.")
    def cmd_set_iacl_seg(self, a):
        need(a, 2, "set_iacl_seg каталог режимы [ident]")
        d = self._dir(a[0])
        check_access(self.proc.subject, d, "m")
        ident = a[2] if len(a) > 2 else self._ident_default()
        d.iacl_seg.set(ident, a[1], False)
        self.out(f"Начальный ACL {d.path}: {'.'.join(parse_ident(ident))} -> {a[1]}")

    @command("list_iacl_seg", "lis", usage="list_iacl_seg [каталог]", group=G_ACL, help="Показать начальный ACL каталога.")
    def cmd_list_iacl_seg(self, a):
        d = self._dir(a[0] if a else None)
        check_access(self.proc.subject, d, "s")
        self.out(f"Начальный ACL для новых сегментов в {d.path}:")
        for e in d.iacl_seg.entries:
            self.out(f"  {self._modes(e.modes):<5} {e.ident}")
        if not d.iacl_seg.entries:
            self.out("  (пусто)")

    @command("delete_iacl_seg", "dis", usage="delete_iacl_seg каталог [Person.Project.tag]", group=G_ACL,
             help="Удалить запись начального ACL.")
    def cmd_delete_iacl_seg(self, a):
        need(a, 1, "delete_iacl_seg каталог [ident]")
        d = self._dir(a[0])
        check_access(self.proc.subject, d, "m")
        ident = a[1] if len(a) > 1 else self._ident_default()
        self.out("Запись удалена" if d.iacl_seg.remove(ident) else "Такой записи нет")

    def _dir(self, path):
        d = self._resolve(path) if path else self.proc.wdir
        if not d.is_dir:
            raise BadArgument(f"{d.path} — не каталог")
        return d

    # ---------------------------------------------------------- кольца
    @command("set_ring_brackets", "srb", usage="set_ring_brackets путь r1 r2 r3", group=G_ACL,
             help="Кольцевые скобки сегмента. Нельзя поставить r1 ниже своего кольца.")
    def cmd_srb(self, a):
        need(a, 4, "set_ring_brackets путь r1 r2 r3")
        seg = self._resolve(a[0])
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог; используйте set_dir_ring_brackets")
        self._set_brackets(seg, RingBrackets(*(to_int(x, "номер кольца") for x in a[1:4])))

    @command("set_dir_ring_brackets", "sdrb", usage="set_dir_ring_brackets каталог r1 r2", group=G_ACL,
             help="Кольцевые скобки каталога: изменение в кольцах <= r1, просмотр <= r2.")
    def cmd_sdrb(self, a):
        need(a, 3, "set_dir_ring_brackets каталог r1 r2")
        d = self._dir(a[0])
        r1, r2 = (to_int(x, "номер кольца") for x in a[1:3])
        self._set_brackets(d, RingBrackets(r1, r2, r2))

    def _set_brackets(self, seg, new):
        self._require_modify(seg)
        if new.r1 < self.proc.ring:
            raise BadArgument(f"r1={new.r1} меньше вашего кольца {self.proc.ring}: "
                              f"нельзя выдать объекту больше привилегий, чем есть у вас")
        seg.brackets = new
        self.out(f"Кольцевые скобки {seg.path}: {new}")

    @command("set_gate", "sg", usage="set_gate путь on|off", group=G_ACL,
             help="Объявить исполняемый сегмент шлюзом (gate).")
    def cmd_set_gate(self, a):
        need(a, 2, "set_gate путь on|off")
        seg = self._resolve(a[0])
        self._require_modify(seg)
        if not seg.executable:
            raise BadArgument("Шлюзом может быть только исполняемый сегмент")
        seg.gate = a[1] == "on"
        self.out(f"{seg.path}: шлюз {'включён' if seg.gate else 'выключен'}")

    @command("ring", usage="ring [N]", group=G_ACL,
             help="Показать кольцо или перейти во внешнее кольцо N (понижать привилегии можно, повышать — нет).")
    def cmd_ring(self, a):
        p = self.proc
        if not a:
            self.out(f"Текущее кольцо процесса: {p.ring}")
            return
        n = to_int(a[0], "номер кольца")
        if not p.ring <= n <= MAX_RING:
            raise BadArgument(f"Кольцо можно только увеличить (>= {p.ring}, <= {MAX_RING}): "
                              f"снизить номер кольца без шлюза нельзя")
        p.ring = n
        self.out(f"Процесс {p.pid} теперь работает в кольце {n}")

    # ---------------------------------------------------- safety switch
    @command("safety_sw_on", "sswn", usage="safety_sw_on путь", group=G_ACL,
             help="Включить safety switch: запись нельзя удалить, пока он включён.")
    def cmd_safety_on(self, a):
        self._safety(a, True)

    @command("safety_sw_off", "sswf", usage="safety_sw_off путь", group=G_ACL, help="Выключить safety switch.")
    def cmd_safety_off(self, a):
        self._safety(a, False)

    def _safety(self, a, value):
        need(a, 1, "safety_sw_on|off путь")
        seg = self._resolve(a[0])
        self._require_modify(seg)
        seg.safety = value
        self.out(f"{seg.path}: safety switch {'включён' if value else 'выключен'}")

    # ---------------------------------------------------- разбор доступа
    @command("effective_access", "ea", usage="effective_access путь", group=G_ACL,
             help="Почему доступ разрешён или запрещён: ACL и кольцо по каждому режиму.")
    def cmd_effective_access(self, a):
        need(a, 1, "effective_access путь")
        seg = self._resolve(a[0])
        subj = self.proc.subject
        granted = seg.acl.lookup(subj.principal)
        self.out(f"{seg.path} для {subj.principal} в кольце {subj.ring} (скобки {seg.brackets}, "
                 f"ACL даёт: {self._modes(granted)})")
        self.out("  режим  ACL  кольцо  итог   пояснение")
        for m in (DIR_MODES if seg.is_dir else SEG_MODES):
            acl_ok = m in granted
            ring_ok, _, why = ring_decision(seg, m, subj.ring)
            mark = lambda ok: "да " if ok else "нет"
            self.out(f"  {m:<5}  {mark(acl_ok)}  {mark(ring_ok)}     {mark(acl_ok and ring_ok)}    {why}")
