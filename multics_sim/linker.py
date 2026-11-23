"""Динамическое связывание (dynamic linking).

Исполняемый сегмент ссылается на другие процедуры по ИМЕНИ. Пока ссылка не
разрешена, при первом вызове происходит linkage fault: ядро ищет сегмент по
правилам поиска (рабочий каталог, затем >sss), проверяет доступ, делает сегмент
известным (KST) и «защёлкивает» (snap) ссылку. Последующие вызовы идут
напрямую, без поиска.
"""
from .access import check_access
from .errors import NotFound
from .process import Link, WDIR


class Linker:
    def __init__(self, system):
        self.system = system

    def rule_dir(self, proc, rule):
        """Каталог, соответствующий правилу поиска."""
        if rule == WDIR:
            return proc.wdir
        d = self.system.fs.resolve(rule, self.system.fs.root)
        if not d.is_dir:
            raise NotFound(f"{rule} — не каталог")
        return d

    def search(self, proc, name, verbose=False):
        """Найти процедурный сегмент по имени или пути согласно правилам поиска."""
        fs, out = self.system.fs, self.system.out
        if ">" in name or "<" in name:
            return fs.resolve(name, proc.wdir)
        for rule in proc.search_rules:
            try:
                directory = self.rule_dir(proc, rule)
            except NotFound:
                if verbose:
                    out(f"      правило {rule}: каталог не существует, пропущено")
                continue
            found = directory.children.get(name)
            if found is not None and found.is_link:
                found = fs.resolve(found.path, fs.root)          # ссылки проходятся
            if found is not None and not found.is_dir:
                if verbose:
                    out(f"      поиск в {directory.path}: найден (сегмент #{found.segno})")
                return found
            if verbose:
                out(f"      поиск в {directory.path}: не найден")
        raise NotFound(f"Сегмент «{name}» не найден по правилам поиска")

    def resolve_call(self, proc, caller, name):
        """Возвращает (сегмент, кольцо выполнения) для вызова name из сегмента caller."""
        key = (caller.segno, name)
        link = proc.linkage.get(key)
        if link is not None and link.snapped:
            self.system.out(f"  ссылка «{name}» уже защёлкнута — прямой вызов, без linkage fault")
            return link.segment, check_access(proc.subject, link.segment, "e")

        out = self.system.out
        out(f"  [ядро, кольцо 0] linkage fault: {caller.name} -> «{name}» (ссылка не разрешена)")
        seg = self.search(proc, name, verbose=True)
        ring = check_access(proc.subject, seg, "e")          # права проверяются до защёлкивания
        if proc.kst.initiate(seg, proc.ring):
            out(f"      сегмент #{seg.segno} ({seg.path}) внесён в KST процесса")
        proc.linkage[key] = Link(name, True, seg)
        out(f"      ссылка «{name}» защёлкнута -> сегмент #{seg.segno}")
        return seg, ring
