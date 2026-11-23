"""Команды файловой системы: каталоги, сегменты, имена, ссылки, обход дерева."""
import difflib

from ..access import check_access
from ..acl import DIR_MODES, SEG_MODES
from ..errors import AccessViolation, BadArgument, MulticsError, to_int
from ..starname import is_starname
from .base import G_FS, command, need

KIND_TAG = {"каталог": "dir ", "данные": "data", "исполняемый": "exec", "шлюз (gate)": "gate", "ссылка": "link"}


def split_text(words):
    """Аргументы-слова -> текст сегмента; ';' означает конец строки."""
    return "\n".join(s.strip() for s in " ".join(words).split(";"))


class FileCommands:
    # ------------------------------------------------------------ просмотр
    @command("list", "ls", usage="list [путь|звёздное_имя] [-dir|-seg|-link]", group=G_FS,
             help="Содержимое каталога (режим s). Поддерживает звёздные имена: list *.txt, list **.")
    def cmd_list(self, a):
        flags = {x for x in a if x in ("-dir", "-seg", "-link")}
        a = [x for x in a if x not in flags]
        fs, subj = self.system.fs, self.proc.subject
        if a and is_starname(a[0]):
            items, title = fs.match(subj, self.proc.wdir, a[0]), f"Совпадения с {a[0]}"
        else:
            node = self._resolve(a[0]) if a else self.proc.wdir
            if not node.is_dir:
                return self.cmd_status([a[0]])
            items, title = fs.list_dir(subj, node), f"Каталог {node.path}"
        if flags:
            items = [s for s in items if ("-dir" in flags and s.is_dir) or ("-link" in flags and s.is_link)
                     or ("-seg" in flags and not s.is_dir and not s.is_link)]
        self.out(f"{title} (записей: {len(items)})")
        if not items:
            return
        self.out("  ACL — что разрешает список доступа; Итог — что реально можно с учётом кольца")
        self.out("  Тип   ACL    Итог   Кольца   Сегм  Размер  Имя")
        for s in items:
            if s.is_link:
                self.out(f"  link  {'-':<5}  {'-':<5}  {'-':<7} {'-':<5} {'-':>6}  {s.name} -> {s.link_target}")
                continue
            acl = self._modes(s.acl.lookup(self.proc.principal))
            size = "-" if s.is_dir else s.size
            extra = f"  (и: {', '.join(s.names[1:])})" if len(s.names) > 1 else ""
            safe = " [safety]" if s.safety else ""
            self.out(f"  {KIND_TAG[s.kind]}  {acl:<5}  {self._effective(s):<5}  {str(s.brackets):<7} "
                     f"#{s.segno:<4} {size!s:>6}  {s.name}{extra}{safe}")

    def _effective(self, seg):
        """Итоговые права: режимы, которые проходят и ACL, и проверку кольца."""
        granted = ""
        for m in (DIR_MODES if seg.is_dir else SEG_MODES):
            try:
                check_access(self.proc.subject, seg, m)
                granted += m
            except AccessViolation:
                pass
        return self._modes(granted)

    @command("status", "st", usage="status путь", group=G_FS, help="Подробные сведения о записи каталога.")
    def cmd_status(self, a):
        need(a, 1, "status путь")
        seg = self.system.fs.resolve(a[0], self.proc.wdir, chase=False)
        check_access(self.proc.subject, self.system.fs.controlling_dir(seg), "s")
        o, mem = self.out, self.system.memory
        o(f"Запись {seg.path}")
        o(f"  имена            : {', '.join(seg.names)}")
        o(f"  тип              : {seg.kind}")
        if seg.is_link:
            o(f"  указывает на     : {seg.link_target}")
            return
        o(f"  номер сегмента   : #{seg.segno}")
        o(f"  владелец         : {seg.owner or '-'}")
        o(f"  кольцевые скобки : {seg.brackets}")
        o(f"  safety switch    : {'включён' if seg.safety else 'выключен'}")
        o(f"  создан / изменён : {seg.created:%d.%m.%Y %H:%M:%S} / {seg.modified:%d.%m.%Y %H:%M:%S}")
        if not seg.is_dir:
            res = mem.resident_pages(seg.segno)
            o(f"  размер           : {seg.size} симв., страниц {mem.pages_of(seg)}, в памяти: {res or 'нет'}")
        else:
            o(f"  записей          : {len(self.system.fs.entries(seg))}")
        o("  ACL:")
        for e in seg.acl.entries:
            o(f"    {self._modes(e.modes):<5} {e.ident}")

    @command("tree", usage="tree [путь] [-depth N]", group=G_FS, help="Дерево каталогов (только доступные по s).")
    def cmd_tree(self, a):
        depth = 99
        if "-depth" in a:
            i = a.index("-depth")
            need(a, i + 2, "tree [путь] [-depth N]")
            depth = to_int(a[i + 1], "глубина", 1)
            a = a[:i] + a[i + 2:]
        node = self._resolve(a[0]) if a else self.proc.wdir
        self.out(node.path)
        self._tree(node, "", 1, depth)

    def _tree(self, node, prefix, level, max_level):
        try:
            items = self.system.fs.list_dir(self.proc.subject, node)
        except AccessViolation:
            self.out(prefix + "└── (нет доступа)")
            return
        for i, s in enumerate(items):
            last = i == len(items) - 1
            label = s.name + (">" if s.is_dir else f" -> {s.link_target}" if s.is_link else f"  ({s.size})")
            self.out(prefix + ("└── " if last else "├── ") + label)
            if s.is_dir and level < max_level:
                self._tree(s, prefix + ("    " if last else "│   "), level + 1, max_level)

    @command("walk_subtree", "ws", usage="walk_subtree путь команда...", group=G_FS,
             help="Выполнить команду в каждом каталоге поддерева (wdir временно меняется).")
    def cmd_walk_subtree(self, a):
        need(a, 2, "walk_subtree путь команда...")
        start, line = self._resolve(a[0]), " ".join(a[1:])
        walker = self.proc                       # процесс, который выполняет обход
        saved = walker.wdir
        try:
            for d in list(self.system.fs.walk(walker.subject, start)):
                if self.proc is not walker:      # команда сменила процесс (logout, switch...)
                    self.out("walk_subtree: текущий процесс сменился — обход прерван")
                    break
                self.out(f"--- {d.path}")
                walker.wdir = d
                self.execute(line)
        finally:
            if walker.pid in self.system.processes:
                walker.wdir = saved

    # ----------------------------------------------------------- каталоги
    @command("change_wdir", "cwd", usage="change_wdir [путь]", group=G_FS,
             help="Сменить рабочий каталог; без аргумента — в каталог по умолчанию.")
    def cmd_cwd(self, a):
        node = self._resolve(a[0]) if a else self.proc.default_wdir
        if not node.is_dir:
            raise BadArgument(f"{node.path} — не каталог")
        check_access(self.proc.subject, node, "s")
        self.proc.wdir = node
        self.out(f"Рабочий каталог: {node.path}")

    @command("print_wdir", "pwd", group=G_FS, help="Показать рабочий каталог.")
    def cmd_pwd(self, a):
        self.out(self.proc.wdir.path)

    @command("change_default_wdir", "cdwd", usage="change_default_wdir [путь]", group=G_FS,
             help="Задать каталог по умолчанию (без аргумента — текущий рабочий).")
    def cmd_cdwd(self, a):
        node = self._resolve(a[0]) if a else self.proc.wdir
        if not node.is_dir:
            raise BadArgument(f"{node.path} — не каталог")
        self.proc.default_wdir = node
        self.out(f"Каталог по умолчанию: {node.path}")

    @command("print_default_wdir", "pdwd", group=G_FS, help="Показать каталог по умолчанию.")
    def cmd_pdwd(self, a):
        self.out(self.proc.default_wdir.path)

    @command("create_dir", "cdir", usage="create_dir путь...", group=G_FS,
             help="Создать каталог(и) (нужен режим a в родителе).")
    def cmd_create_dir(self, a):
        need(a, 1, "create_dir путь...")
        for path in a:
            seg = self.system.fs.create(self.proc.subject, self.proc.wdir, path, is_dir=True)
            self.out(f"Создан каталог {seg.path} (сегмент #{seg.segno}), скобки {seg.brackets}")

    @command("delete_dir", "dd", usage="delete_dir путь [-force]", group=G_FS,
             help="Удалить каталог; непустой — только с -force (рекурсивно).")
    def cmd_delete_dir(self, a):
        force = "-force" in a
        a = [x for x in a if x != "-force"]
        need(a, 1, "delete_dir путь [-force]")
        seg = self.system.fs.delete(self.proc.subject, self.proc.wdir, a[0], True, force)
        self.out(f"Удалён каталог {seg.name}")

    # -------------------------------------------------------------- квоты
    @command("set_quota", usage="set_quota каталог N|-none", group=G_FS,
             help="Квота каталога в страницах на всё поддерево (нужен режим m на родительском каталоге).")
    def cmd_set_quota(self, a):
        need(a, 2, "set_quota каталог N|-none")
        d = self._resolve(a[0])
        if not d.is_dir:
            raise BadArgument(f"{d.path} — не каталог")
        check_access(self.proc.subject, self.system.fs.controlling_dir(d), "m")
        if a[1] == "-none":
            d.quota = None
            self.out(f"Квота {d.path} снята")
            return
        n, used = to_int(a[1], "квота"), self.system.fs.usage(d)
        if n < used:
            raise BadArgument(f"В {d.path} уже занято {used} стр. — квота не может быть меньше")
        d.quota = n
        self.out(f"Квота {d.path}: {n} стр. (занято {used})")

    @command("get_quota", "gq", usage="get_quota [каталог]", group=G_FS,
             help="Квота и занятое место каталога и ближайших ограничивающих каталогов.")
    def cmd_get_quota(self, a):
        d = self._resolve(a[0]) if a else self.proc.wdir
        if not d.is_dir:
            raise BadArgument(f"{d.path} — не каталог")
        fs, shown = self.system.fs, False
        p = d
        while p is not None:
            if p.quota is not None or p is d:
                q = f"{p.quota} стр." if p.quota is not None else "нет"
                self.out(f"  {p.path}: квота {q}, занято {fs.usage(p)} стр.")
                shown = shown or p.quota is not None
            p = p.parent
        if not shown:
            self.out("  Ограничивающих квот нет.")

    # ----------------------------------------------------------- сегменты
    @command("create", "cr", usage='create [-exec] [-gate] путь ["текст; строка 2"]', group=G_FS,
             help="Создать сегмент. -exec — исполняемый (текст — мини-скрипт), -gate — шлюз.")
    def cmd_create(self, a):
        executable = gate = False
        while a and a[0] in ("-exec", "-x", "-gate"):
            flag = a.pop(0)
            executable = True if flag in ("-exec", "-x", "-gate") else executable
            gate |= flag == "-gate"
        need(a, 1, "create [-exec] [-gate] путь [текст]")
        text = split_text(a[1:]) if len(a) > 1 else ""
        seg = self.system.fs.create(self.proc.subject, self.proc.wdir, a[0], content=text,
                                    executable=executable, gate=gate)
        if text:
            self.system.reference(self.proc, seg, "w")
        self.out(f"Создан сегмент ({seg.kind}) {seg.path} (сегмент #{seg.segno}), скобки {seg.brackets}")

    @command("delete", "dl", usage="delete путь|звёздное_имя...", group=G_FS,
             help="Удалить сегменты (режим m в родителе; safety switch запрещает удаление).")
    def cmd_delete(self, a):
        need(a, 1, "delete путь...")
        fs = self.system.fs
        for path in a:
            targets = ([s.path for s in fs.match(self.proc.subject, self.proc.wdir, path)
                        if not s.is_dir and not s.is_link] if is_starname(path) else [path])
            if not targets:
                self.out(f"Нет сегментов, подходящих под {path}")
            for t in targets:
                try:
                    seg = fs.delete(self.proc.subject, self.proc.wdir, t, False)
                    self.out(f"Удалён сегмент {seg.name}")
                except MulticsError as e:
                    self.out(f"Ошибка: {e}")

    @command("print", "pr", usage="print [-q] путь [с_строки [по_строку]]", group=G_FS,
             help="Вывести сегмент (режим r); можно диапазон строк; -q — только обратиться к страницам.")
    def cmd_print(self, a):
        quiet = bool(a) and a[0] == "-q"
        a = a[1:] if quiet else a
        need(a, 1, "print [-q] путь [с [по]]")
        seg = self._resolve(a[0])
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог; используйте list")
        self.system.reference(self.proc, seg, "r")
        if quiet:
            return
        if len(a) == 1:
            self.out(seg.content if seg.content else "(пусто)")
            return
        lines = seg.content.split("\n")
        first = to_int(a[1], "номер строки", 1)
        last = to_int(a[2], "номер строки", 1) if len(a) > 2 else first
        for n in range(first, min(last, len(lines)) + 1):
            self.out(f"{n:>4}  {lines[n - 1]}")

    @command("write", "wr", usage="write путь текст", group=G_FS,
             help="Заменить содержимое сегмента (режим w; ';' — конец строки).")
    def cmd_write(self, a):
        need(a, 2, "write путь текст")
        seg = self._resolve(a[0])
        self.system.set_content(self.proc, seg, split_text(a[1:]))
        self.out(f"Записано {seg.size} символов в {seg.path}")

    @command("append", "ap", usage="append путь текст", group=G_FS,
             help="Дописать строки в конец сегмента (режим w).")
    def cmd_append(self, a):
        need(a, 2, "append путь текст")
        seg = self._resolve(a[0])
        check_access(self.proc.subject, seg, "r")
        text = seg.content + ("\n" if seg.content else "") + split_text(a[1:])
        self.system.set_content(self.proc, seg, text)
        self.out(f"{seg.path}: теперь {seg.size} символов")

    @command("truncate", "tc", usage="truncate путь [длина]", group=G_FS,
             help="Усечь сегмент до заданной длины (по умолчанию до нуля); лишние страницы освобождаются.")
    def cmd_truncate(self, a):
        need(a, 1, "truncate путь [длина]")
        seg = self._resolve(a[0])
        n = to_int(a[1], "длина") if len(a) > 1 else 0
        self.system.set_content(self.proc, seg, seg.content[:n])
        self.out(f"{seg.path}: {seg.size} символов")

    @command("fill", usage="fill путь N", group=G_FS,
             help="Заполнить сегмент N символами тестовых данных (для опытов с памятью).")
    def cmd_fill(self, a):
        need(a, 2, "fill путь N")
        seg = self._resolve(a[0])
        n, text, i = to_int(a[1], "размер", 1), "", 0
        while len(text) < n:
            text += f"Данные {i:04d}\n"
            i += 1
        self.system.set_content(self.proc, seg, text[:n])
        self.out(f"Сегмент {seg.path}: {seg.size} символов, {self.system.memory.pages_of(seg)} стр.")

    @command("sort_seg", "ss", usage="sort_seg путь [-rev]", group=G_FS,
             help="Отсортировать строки сегмента (режимы r и w).")
    def cmd_sort_seg(self, a):
        need(a, 1, "sort_seg путь [-rev]")
        seg = self._resolve(a[0])
        check_access(self.proc.subject, seg, "r")
        lines = sorted(seg.content.split("\n"), reverse="-rev" in a)
        self.system.set_content(self.proc, seg, "\n".join(lines))
        self.out(f"{seg.path}: отсортировано строк: {len(lines)}")

    @command("compare_ascii", "cpa", usage="compare_ascii путь1 путь2", group=G_FS,
             help="Сравнить два текстовых сегмента построчно.")
    def cmd_compare_ascii(self, a):
        need(a, 2, "compare_ascii путь1 путь2")
        s1, s2 = self._resolve(a[0]), self._resolve(a[1])
        for s in (s1, s2):
            self.system.reference(self.proc, s, "r")
        l1, l2 = s1.content.split("\n"), s2.content.split("\n")
        if l1 == l2:
            self.out("Сегменты идентичны.")
            return
        for line in difflib.unified_diff(l1, l2, s1.path, s2.path, lineterm="", n=1):
            self.out(line)

    # -------------------------------------------------- имена, копии, ссылки
    @command("rename", "rn", usage="rename путь новое_имя", group=G_FS, help="Переименовать запись (режим m).")
    def cmd_rename(self, a):
        need(a, 2, "rename путь новое_имя")
        seg = self.system.fs.rename(self.proc.subject, self.proc.wdir, a[0], a[1])
        self.out(f"Переименовано: {seg.path}")

    @command("add_name", "an", usage="add_name путь имя", group=G_FS,
             help="Добавить записи ещё одно имя (в Multics у сегмента может быть много имён).")
    def cmd_add_name(self, a):
        need(a, 2, "add_name путь имя")
        seg = self.system.fs.add_name(self.proc.subject, self.proc.wdir, a[0], a[1])
        self.out(f"{seg.path}: имена {', '.join(seg.names)}")

    @command("delete_name", "dn", usage="delete_name путь", group=G_FS,
             help="Удалить одно из имён записи (последнее имя удалить нельзя).")
    def cmd_delete_name(self, a):
        need(a, 1, "delete_name путь")
        seg = self.system.fs.delete_name(self.proc.subject, self.proc.wdir, a[0])
        self.out(f"{seg.path}: имена {', '.join(seg.names)}")

    @command("copy", "cp", usage="copy откуда куда", group=G_FS,
             help="Копировать сегмент (r на исходный, a на каталог назначения).")
    def cmd_copy(self, a):
        need(a, 2, "copy откуда куда")
        src, new = self.system.fs.copy(self.proc.subject, self.proc.wdir, a[0], a[1])
        self.system.reference(self.proc, src, "r")
        if new.content:
            self.system.reference(self.proc, new, "w")
        self.out(f"Скопировано: {src.path} -> {new.path} (новый сегмент #{new.segno})")

    @command("copy_dir", "cpd", usage="copy_dir откуда куда", group=G_FS, help="Рекурсивно скопировать каталог.")
    def cmd_copy_dir(self, a):
        need(a, 2, "copy_dir откуда куда")
        n = self.system.fs.copy_dir(self.proc.subject, self.proc.wdir, a[0], a[1])
        self.out(f"Каталог скопирован, записей: {n}")

    @command("move", "mv", usage="move откуда куда", group=G_FS,
             help="Переместить запись (m в старом каталоге, a в новом); номер сегмента сохраняется.")
    def cmd_move(self, a):
        need(a, 2, "move откуда куда")
        seg = self.system.fs.move(self.proc.subject, self.proc.wdir, a[0], a[1])
        self.out(f"Перемещено: {seg.path} (сегмент #{seg.segno})")

    @command("link", "lk", usage="link цель [путь_ссылки]", group=G_FS,
             help="Создать ссылку (link) на другой объект; пути через неё проходятся автоматически.")
    def cmd_link(self, a):
        need(a, 1, "link цель [путь_ссылки]")
        target = a[0]
        name = a[1] if len(a) > 1 else target.replace("<", ">").split(">")[-1]
        seg = self.system.fs.create_link(self.proc.subject, self.proc.wdir, target, name)
        self.out(f"Создана ссылка {seg.path} -> {seg.link_target}")

    @command("unlink", "ul", usage="unlink путь", group=G_FS, help="Удалить ссылку.")
    def cmd_unlink(self, a):
        need(a, 1, "unlink путь")
        seg = self.system.fs.unlink(self.proc.subject, self.proc.wdir, a[0])
        self.out(f"Ссылка {seg.name} удалена")
