"""Иерархическая файловая система с разделителем '>' ('<' — подняться на уровень вверх).

Класс отвечает за разбор путей и операции над деревом; права проверяются через
access.check_access. Упрощение: права на промежуточные каталоги пути при
разборе имени не проверяются — проверяются права на сам объект и на его
родительский каталог (как в командах create/delete/list).

Поддерживаются особенности Multics: несколько имён у одной записи, ссылки
(links) с автоматическим «прохождением» (chasing), safety switch и начальные
ACL каталога (initial ACL), которые получает каждый новый сегмент.
"""
import re

from .access import Subject, check_access
from .errors import AccessViolation, AlreadyExists, BadArgument, MulticsError, NotFound
from .rings import RingBrackets
from .segment import Segment
from .starname import star_match

_TOKEN = re.compile(r"[<>]|[^<>]+")
MAX_LINK_DEPTH = 10


class FileSystem:
    def __init__(self, page_size=64):
        self.page_size = page_size   # для учёта квот (место считается страницами)
        self._next_segno = 0
        self.root = self._new("", None, True, RingBrackets(0, 7, 7))
        self.before_delete = []  # проверки перед удалением (могут бросить ошибку)
        self.on_delete = []      # подписчики: вызываются после удаления сегмента

    def _new(self, name, parent, is_dir, brackets):
        seg = Segment(self._next_segno, name, parent, is_dir, brackets)
        self._next_segno += 1
        return seg

    # ---------------------------------------------------------- служебное
    @staticmethod
    def join(directory, name):
        return (">" if directory.parent is None else directory.path + ">") + name

    @staticmethod
    def validate_name(name):
        if not name or len(name) > 32 or re.search(r"[<>\s\[\]*?]", name):
            raise BadArgument(f"Недопустимое имя «{name}» (до 32 символов, без пробелов и <>[]*?)")

    @staticmethod
    def entries(directory):
        """Уникальные записи каталога (у одной записи может быть несколько имён)."""
        seen, out = set(), []
        for seg in directory.children.values():
            if id(seg) not in seen:
                seen.add(id(seg))
                out.append(seg)
        return out

    def _attach(self, parent, seg):
        seg.parent = parent
        for n in seg.names:
            parent.children[n] = seg

    @staticmethod
    def _detach(seg):
        for n in seg.names:
            seg.parent.children.pop(n, None)

    @staticmethod
    def controlling_dir(seg):
        """Каталог, чьё право 'm' нужно для смены ACL/скобок объекта."""
        return seg.parent or seg

    # ---------------------------------------------------------- разбор путей
    def resolve(self, path, wdir, chase=True, _depth=0):
        """Путь -> Segment. Абсолютный начинается с '>', иначе от wdir.
        Ссылки в середине пути проходятся всегда, последняя — если chase=True."""
        if not path:
            raise BadArgument("Пустой путь")
        tokens = _TOKEN.findall(path)
        last = max((i for i, t in enumerate(tokens) if t not in "<>"), default=-1)
        node = self.root if tokens[0] == ">" else wdir
        for i, t in enumerate(tokens):
            if t == ">":
                continue
            if t == "<":
                if node.parent is None:
                    raise NotFound("У корня «>» нет родительского каталога")
                node = node.parent
                continue
            if not node.is_dir:
                raise NotFound(f"{node.path} — не каталог, нельзя искать в нём «{t}»")
            child = node.children.get(t)
            if child is None:
                raise NotFound(f"Не найдено: {path} (нет «{t}» в {node.path})")
            if child.is_link and (chase or i != last):
                child = self._chase(child, _depth)
            node = child
        return node

    def _chase(self, link, depth):
        if depth >= MAX_LINK_DEPTH:
            raise MulticsError(f"Слишком длинная цепочка ссылок (возможно, цикл) у {link.path}")
        try:
            return self.resolve(link.link_target, self.root, True, depth + 1)
        except NotFound:
            raise NotFound(f"Ссылка {link.path} указывает на несуществующий {link.link_target}") from None

    def split_parent(self, path, wdir):
        """'>a>b>c' -> (каталог '>a>b', 'c'). Для имени без разделителей родитель — wdir."""
        idx = max(path.rfind(">"), path.rfind("<"))
        if idx < 0:
            return wdir, path
        sep = path[idx]
        parent_path = path[: idx + 1] if sep == "<" else (path[:idx] or ">")
        return self.resolve(parent_path, wdir), path[idx + 1:]

    def match(self, subject, wdir, pattern):
        """Записи, подходящие под звёздное имя ('*.txt', '>udd>**')."""
        directory, star = self.split_parent(pattern, wdir)
        check_access(subject, directory, "s")
        found = [s for s in self.entries(directory) if any(star_match(star, n) for n in s.names)]
        return sorted(found, key=lambda s: s.name)

    # ------------------------------------------------------------------ квоты
    def pages(self, seg):
        """Сколько страниц диска занимает сегмент (пустой — ноль)."""
        return -(-seg.size // self.page_size) if not seg.is_dir and not seg.is_link else 0

    def usage(self, directory):
        """Занято страниц во всём поддереве каталога (ссылки не учитываются)."""
        return sum(self.usage(s) if s.is_dir else self.pages(s) for s in self.entries(directory))

    def check_quota(self, directory, delta, moving=None):
        """Проверить, что добавление delta страниц в directory не превысит квоту
        ни одного каталога-предка. При переносе (moving) каталоги, где запись уже
        лежит, пропускаются: для них место не меняется."""
        if delta <= 0:
            return
        d = directory
        while d is not None:
            if d.quota is not None and not (moving is not None and self._contains(d, moving)):
                used = self.usage(d)
                if used + delta > d.quota:
                    raise MulticsError(f"Превышена квота каталога {d.path}: занято {used} из {d.quota} стр., "
                                       f"нужно ещё {delta}")
            d = d.parent

    @staticmethod
    def _contains(directory, seg):
        p = seg.parent
        while p is not None:
            if p is directory:
                return True
            p = p.parent
        return False

    # --------------------------------------------------------------- создание
    def create(self, subject: Subject, wdir, path, is_dir=False, content="",
               executable=False, gate=False, link_target=None):
        """Создаёт сегмент/каталог/ссылку. Нужен режим 'a' на родительском каталоге."""
        parent, name = self.split_parent(path, wdir)
        if not parent.is_dir:
            raise NotFound(f"{parent.path} — не каталог")
        self.validate_name(name)
        check_access(subject, parent, "a")
        if name in parent.children:
            raise AlreadyExists(f"Имя «{name}» уже существует в {parent.path}")
        self.check_quota(parent, -(-len(content) // self.page_size))
        ring = subject.ring
        seg = self._new(name, parent, is_dir,
                        RingBrackets(ring, 7, 7) if is_dir else RingBrackets(ring, ring, ring))
        seg.owner = str(subject.principal)
        seg.executable, seg.gate, seg.content = executable, gate, content
        seg.link_target = link_target
        who, proj = subject.principal.person, subject.principal.project
        if is_dir:
            seg.acl.set(f"{who}.{proj}.*", "sma", True)
            seg.acl.set(f"*.{proj}.*", "s", True)
        elif link_target is None:
            seg.acl.set(f"{who}.{proj}.*", "rew", False)
            for e in parent.iacl_seg.entries:            # начальный ACL каталога
                seg.acl.set(e.ident, e.modes, False)
        self._attach(parent, seg)
        return seg

    def create_link(self, subject, wdir, target, path):
        if not target.startswith(">"):
            target = self.join(wdir, target)       # ссылки хранят абсолютный путь
        return self.create(subject, wdir, path, link_target=target)

    def copy(self, subject, wdir, src, dst):
        """Копия сегмента (нужны r на исходный и a на каталог назначения)."""
        s = self.resolve(src, wdir)
        if s.is_dir:
            raise BadArgument(f"{s.path} — каталог; используйте copy_dir")
        check_access(subject, s, "r")
        target = dst
        try:
            d = self.resolve(dst, wdir)
            if not d.is_dir:
                raise AlreadyExists(f"{d.path} уже существует")
            target = self.join(d, s.name)
        except NotFound:
            pass
        return s, self.create(subject, wdir, target, content=s.content, executable=s.executable)

    def copy_dir(self, subject, wdir, src, dst):
        """Рекурсивное копирование каталога. Возвращает число скопированных записей."""
        s = self.resolve(src, wdir)
        if not s.is_dir:
            raise BadArgument(f"{s.path} — не каталог")
        check_access(subject, s, "s")
        p = self.split_parent(dst, wdir)[0]
        while p is not None:
            if p is s:
                raise BadArgument("Нельзя копировать каталог внутрь самого себя")
            p = p.parent
        new = self.create(subject, wdir, dst, is_dir=True)
        count = 0
        for e in self.entries(s):
            target = self.join(new, e.name)
            if e.is_link:
                self.create(subject, wdir, target, link_target=e.link_target)
            elif e.is_dir:
                count += self.copy_dir(subject, wdir, e.path, target)
            else:
                check_access(subject, e, "r")
                self.create(subject, wdir, target, content=e.content, executable=e.executable)
            count += 1
        return count

    # --------------------------------------------------------- имена и места
    def rename(self, subject, wdir, path, new_name):
        parent, old = self.split_parent(path, wdir)
        seg = self.resolve(path, wdir, chase=False)
        if seg.parent is None:
            raise BadArgument("Корень переименовать нельзя")
        self.validate_name(new_name)
        check_access(subject, seg.parent, "m")
        if new_name in seg.parent.children:
            raise AlreadyExists(f"Имя «{new_name}» уже существует в {seg.parent.path}")
        if old not in seg.names:
            old = seg.name
        del seg.parent.children[old]
        seg.names[seg.names.index(old)] = new_name
        seg.parent.children[new_name] = seg
        return seg

    def add_name(self, subject, wdir, path, new_name):
        seg = self.resolve(path, wdir, chase=False)
        if seg.parent is None:
            raise BadArgument("Корню нельзя добавить имя")
        self.validate_name(new_name)
        check_access(subject, seg.parent, "m")
        if new_name in seg.parent.children:
            raise AlreadyExists(f"Имя «{new_name}» уже существует в {seg.parent.path}")
        seg.names.append(new_name)
        seg.parent.children[new_name] = seg
        return seg

    def delete_name(self, subject, wdir, path):
        parent, nm = self.split_parent(path, wdir)
        seg = parent.children.get(nm)
        if seg is None:
            raise NotFound(f"Нет имени «{nm}» в {parent.path}")
        if len(seg.names) == 1:
            raise BadArgument(f"«{nm}» — единственное имя {seg.path}; используйте delete")
        check_access(subject, parent, "m")
        seg.names.remove(nm)
        del parent.children[nm]
        return seg

    def move(self, subject, wdir, src, dst):
        """Перенос записи в другой каталог (и/или под другим именем)."""
        seg = self.resolve(src, wdir, chase=False)
        if seg.parent is None:
            raise BadArgument("Корень переместить нельзя")
        try:
            d = self.resolve(dst, wdir)
            if not d.is_dir:
                raise AlreadyExists(f"{d.path} уже существует")
            new_parent, new_name = d, seg.name
        except NotFound:
            new_parent, new_name = self.split_parent(dst, wdir)
            self.validate_name(new_name)
        if not new_parent.is_dir:
            raise BadArgument(f"{new_parent.path} — не каталог")
        p = new_parent
        while p is not None:
            if p is seg:
                raise BadArgument("Нельзя переместить каталог внутрь самого себя")
            p = p.parent
        check_access(subject, seg.parent, "m")
        check_access(subject, new_parent, "a")
        self.check_quota(new_parent, self.usage(seg) if seg.is_dir else self.pages(seg), moving=seg)
        new_names = [new_name] + [n for n in seg.names[1:] if n != new_name]
        for n in new_names:
            if n in new_parent.children and new_parent.children[n] is not seg:
                raise AlreadyExists(f"Имя «{n}» уже существует в {new_parent.path}")
        self._detach(seg)
        seg.names = new_names
        self._attach(new_parent, seg)
        return seg

    # --------------------------------------------------------------- удаление
    def delete(self, subject, wdir, path, want_dir, force=False):
        """Удаление. Нужен режим 'm' на родительском каталоге.
        Непустой каталог удаляется только с force=True (рекурсивно)."""
        seg = self.resolve(path, wdir, chase=False)
        if seg.parent is None:
            raise BadArgument("Корневой каталог удалить нельзя")
        if seg.is_link:
            raise BadArgument(f"{seg.path} — ссылка; используйте unlink")
        if seg.is_dir != want_dir:
            raise BadArgument(f"{seg.path} — {'каталог, используйте delete_dir' if seg.is_dir else 'не каталог, используйте delete'}")
        check_access(subject, seg.parent, "m")
        if seg.is_dir and seg.children and not force:
            raise BadArgument(f"Каталог {seg.path} не пуст (delete_dir {path} -force — удалить вместе с содержимым)")
        self._check_deletable(subject, seg)          # сначала проверить всё поддерево
        self._remove_tree(seg)
        return seg

    def _check_deletable(self, subject, seg):
        if seg.safety:
            raise AccessViolation(f"У {seg.path} включён safety switch — удаление запрещено (safety_sw_off)")
        for hook in self.before_delete:
            hook(seg)
        if seg.is_dir:
            if seg.children:
                check_access(subject, seg, "m")
            for child in self.entries(seg):
                if not child.is_link:
                    self._check_deletable(subject, child)

    def _remove_tree(self, seg):
        if seg.is_dir:
            for child in self.entries(seg):
                self._remove_tree(child)
        self._detach(seg)
        for hook in self.on_delete:
            hook(seg)

    def unlink(self, subject, wdir, path):
        seg = self.resolve(path, wdir, chase=False)
        if not seg.is_link:
            raise BadArgument(f"{seg.path} — не ссылка")
        check_access(subject, seg.parent, "m")
        self._detach(seg)
        return seg

    def list_dir(self, subject, directory):
        """Содержимое каталога (нужен режим 's'): каталоги, сегменты, ссылки."""
        check_access(subject, directory, "s")
        order = lambda s: (0 if s.is_dir else 2 if s.is_link else 1, s.name)
        return sorted(self.entries(directory), key=order)

    def walk(self, subject, directory):
        """Обход поддерева в глубину (только каталоги с правом 's')."""
        yield directory
        try:
            items = self.list_dir(subject, directory)
        except AccessViolation:
            return
        for s in items:
            if s.is_dir:
                yield from self.walk(subject, s)
