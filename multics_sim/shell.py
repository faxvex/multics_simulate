"""Командный интерпретатор (командный процессор) в стиле Multics.

Порядок обработки строки:
  1. сокращения (abbrev): первое слово заменяется, если для него задано сокращение;
  2. активные функции: [имя аргументы] вычисляются изнутри наружу, результат
     подставляется в строку (|[...] — одним аргументом в кавычках);
  3. разбор на аргументы (кавычки "...", "" внутри — сама кавычка);
  4. вызов команды. Если такой команды нет, но есть активная функция с этим
     именем, она вызывается и результат печатается.

Сами команды разнесены по модулям пакета commands (классы-примеси), Shell
наследует их все. Права проверяются не здесь, а в слоях access/filesystem/system.
"""
from .commands.acl_cmds import AccessCommands
from .commands.af import ActiveFunctions
from .commands.base import matching_bracket, quote_token, tokenize
from .commands.fs_cmds import FileCommands
from .commands.mem_cmds import MemoryCommands
from .commands.misc_cmds import MiscCommands
from .commands.proc_cmds import ProcessCommands
from .commands.user_cmds import UserCommands
from .errors import BadArgument, MulticsError, NotFound
from .system import System

MAX_AF_DEPTH = 20
MASK = "****"
# команда -> номера аргументов-паролей (0 — имя команды)
SECRET_ARGS = {"login": (2,), "l": (2,), "lg": (2,), "change_password": (1, 2), "cpw": (1, 2),
               "new_user": (2,), "nu": (2,)}


def mask_secrets(line):
    """Скрыть пароли перед записью строки в историю команд."""
    words = line.split()
    for i in SECRET_ARGS.get(words[0], ()):
        if i < len(words):
            words[i] = MASK
    return " ".join(words) if words[0] in SECRET_ARGS else line


class Shell(FileCommands, AccessCommands, ProcessCommands, MemoryCommands,
            UserCommands, ActiveFunctions, MiscCommands):
    def __init__(self, system: System = None, out=print, input_fn=input):
        self.out = out
        self.input_fn = input_fn
        self.system = system or System(out)
        self.stack = []        # вошедшие процессы; последний — текущий
        self.running = True
        self.history = []
        self._ec_depth = 0
        self.commands = {}     # имя или алиас -> (метод, Meta)
        self.actives = {}      # имя или алиас -> (метод, Meta)
        for attr in dir(type(self)):
            fn = getattr(self, attr)
            for meta_attr, registry in (("cmd_meta", self.commands), ("af_meta", self.actives)):
                meta = getattr(fn, meta_attr, None)
                if meta:
                    for n in (meta.name,) + tuple(meta.aliases):
                        if n in registry:
                            raise RuntimeError(f"Повтор имени {n}")
                        registry[n] = (fn, meta)

    # ------------------------------------------------------------- сервис
    @property
    def proc(self):
        return self.stack[-1] if self.stack else None

    def prompt(self):
        p = self.proc
        if p is None:
            return "Multics> "
        return f"{p.principal.person}.{p.principal.project} {p.wdir.path} [кольцо {p.ring}]$ "

    def _resolve(self, path):
        return self.system.fs.resolve(path, self.proc.wdir)

    def _find_program(self, name):
        """Для run/start: путь либо поиск по правилам поиска процесса."""
        return self.system.linker.search(self.proc, name)

    @staticmethod
    def _modes(m):
        return m or "null"

    # --------------------------------------------------- выполнение строки
    def execute(self, line):
        """Разобрать и выполнить одну строку. Ошибки ОС печатаются, а не бросаются."""
        line = line.replace("﻿", "").strip()   # BOM от консоли/файла
        if not line or line.startswith("#"):
            return
        if self._ec_depth == 0 and not line.startswith("redo"):
            self.history.append(mask_secrets(line))
        try:
            line = self.expand(self._apply_abbrev(line))
            argv = tokenize(line)
            if not argv:
                return
            self._dispatch(argv)
        except MulticsError as e:
            self.out(f"Ошибка: {e}")
        except RecursionError:
            self.out("Ошибка: слишком глубокая рекурсия (сценарии или сокращения вызывают сами себя)")
        except Exception as e:                       # сбой эмулятора не должен ронять сеанс
            self.out(f"Внутренняя ошибка эмулятора: {type(e).__name__}: {e}")
        if self.proc is not None and self.proc.accept_messages:
            self._show_messages()
        if self.proc is not None and self.proc.ready and self._ec_depth == 0:
            self.print_ready()

    def _dispatch(self, argv):
        name, args = argv[0], argv[1:]
        entry = self.commands.get(name)
        if entry is None and name in self.actives:
            fn, meta = self.actives[name]
            self._check_login(meta)
            self.out(fn(args))
            return
        if entry is None:
            raise NotFound(f"Неизвестная команда «{name}». Список команд: help (поиск: help *{name}*)")
        fn, meta = entry
        self._check_login(meta)
        fn(args)

    def _check_login(self, meta):
        if meta.login and self.proc is None:
            raise MulticsError("Сначала выполните вход: login Person.Project [пароль]")

    def _apply_abbrev(self, line):
        if self.proc is None:
            return line
        table = self.system.abbrevs.get(self.system.key(self.proc.principal), {})
        first, _, rest = line.partition(" ")
        return f"{table[first]} {rest}".strip() if first in table else line

    def expand(self, line, depth=0):
        """Вычислить все активные функции [..] в строке."""
        if "[" not in line:
            return line
        if depth > MAX_AF_DEPTH:
            raise MulticsError("Слишком глубокая вложенность активных функций")
        out, i, quote = [], 0, False
        while i < len(line):
            c = line[i]
            if c == '"':
                quote = not quote
            elif c == "[" and not quote:
                j = matching_bracket(line, i)
                value = self.call_active(self.expand(line[i + 1:j], depth + 1))
                if out and out[-1] == "|":          # |[...] — один аргумент
                    out[-1] = quote_token(value)
                else:
                    out.append(value)
                i = j + 1
                continue
            out.append(c)
            i += 1
        return "".join(out)

    def call_active(self, text):
        argv = tokenize(text)
        if not argv:
            raise BadArgument("Пустые скобки [] — нет имени активной функции")
        entry = self.actives.get(argv[0])
        if entry is None:
            raise NotFound(f"Нет активной функции «{argv[0]}» (список: help -af)")
        fn, meta = entry
        self._check_login(meta)
        return fn(argv[1:])

    def repl(self):
        """Интерактивный цикл."""
        self.out("Учебный эмулятор Multics. Введите help, help -af или demo. Выход: exit.")
        while self.running:
            try:
                self.execute(self.input_fn(self.prompt()))
            except KeyboardInterrupt:          # Ctrl+C — отменить строку, не выходить
                print("^C")
            except EOFError:                   # Ctrl+Z / конец ввода — выход
                print()
                break
