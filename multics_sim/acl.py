"""Списки управления доступом (ACL) и идентификатор пользователя.

Идентификатор в Multics имеет вид  Person.Project.tag  (tag — 'a' для
интерактивного процесса). В записи ACL любая из трёх частей может быть '*'.
Если подходит несколько записей, побеждает самая конкретная.

Режимы доступа:
  сегмент  : r (чтение), e (исполнение), w (запись)
  каталог  : s (status — просмотр), m (modify — изменение), a (append — добавление)
"""
from dataclasses import dataclass

from .errors import BadArgument

SEG_MODES = "rew"
DIR_MODES = "sma"


@dataclass(frozen=True)
class Principal:
    """Кто работает в системе: Person.Project.tag."""
    person: str
    project: str
    tag: str = "a"

    def __str__(self):
        return f"{self.person}.{self.project}.{self.tag}"


def parse_ident(text):
    """'Ivanov.Students' -> ('Ivanov','Students','*'); недостающие части = '*'."""
    parts = text.split(".")
    if len(parts) > 3 or any(p == "" for p in parts):
        raise BadArgument(f"Неверный идентификатор «{text}», ожидается Person.Project.tag")
    while len(parts) < 3:
        parts.append("*")
    return tuple(parts)


def normalize_modes(modes, is_dir):
    """Проверяет строку режимов и приводит к каноническому порядку."""
    if modes.lower() in ("n", "null", "-"):
        return ""
    allowed = DIR_MODES if is_dir else SEG_MODES
    bad = [c for c in modes if c not in allowed]
    if bad:
        kind = "каталога" if is_dir else "сегмента"
        raise BadArgument(f"Недопустимые режимы «{''.join(bad)}» для {kind}; допустимы: {allowed} или null")
    return "".join(c for c in allowed if c in modes)


@dataclass
class AclEntry:
    person: str
    project: str
    tag: str
    modes: str

    @property
    def ident(self):
        return f"{self.person}.{self.project}.{self.tag}"

    def matches(self, p: Principal):
        return (self.person in ("*", p.person) and self.project in ("*", p.project)
                and self.tag in ("*", p.tag))

    def specificity(self):
        # Person важнее Project, Project важнее tag
        return (self.person != "*", self.project != "*", self.tag != "*")


class Acl:
    """Список записей доступа одного сегмента/каталога."""

    def __init__(self):
        self.entries = []

    def set(self, ident, modes, is_dir):
        person, project, tag = parse_ident(ident)
        modes = normalize_modes(modes, is_dir)
        for e in self.entries:
            if (e.person, e.project, e.tag) == (person, project, tag):
                e.modes = modes
                return
        self.entries.append(AclEntry(person, project, tag, modes))

    def remove(self, ident):
        person, project, tag = parse_ident(ident)
        before = len(self.entries)
        self.entries = [e for e in self.entries if (e.person, e.project, e.tag) != (person, project, tag)]
        return len(self.entries) != before

    def lookup(self, principal):
        """Режимы, которые ACL даёт данному пользователю (самая конкретная запись)."""
        best = None
        for e in self.entries:
            if e.matches(principal) and (best is None or e.specificity() > best.specificity()):
                best = e
        return best.modes if best else ""
