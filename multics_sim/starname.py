"""Звёздные имена (starnames) Multics: шаблоны имён в командах list, delete и т.п.

Имя состоит из компонентов, разделённых точкой (например, report.old.txt):
  *   — любой текст внутри одного компонента (не пересекает '.')
  **  — любое число любых компонентов
  ?   — ровно один символ (не '.')
"""
import re


def is_starname(text):
    return "*" in text or "?" in text


def compile_star(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^.]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^.]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def star_match(pattern, name):
    return compile_star(pattern).match(name) is not None
