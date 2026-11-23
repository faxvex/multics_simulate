"""Активные функции (active functions) Multics.

Активная функция пишется в квадратных скобках прямо в командной строке:
    echo Сейчас [time], каталог [wd]
    delete [segments *.tmp]
    if [exists segment notes] -then "print notes" -else "echo нет"
Перед выполнением команды скобки вычисляются (изнутри наружу) и заменяются
результатом. Форма |[...] вставляет результат одним аргументом (в кавычках).
Любую активную функцию можно вызвать и как команду — результат печатается.
"""
import ast
import math
import operator
import uuid
from datetime import datetime

from ..errors import BadArgument, MulticsError, NotFound, to_int
from .base import (A_ARITH, A_INFO, A_LOGIC, A_PATH, A_STR, FALSE, TRUE, active, command,
                   fmt_number, from_bool, need, parse_number, to_bool, G_SHELL)

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


# ---------------------------------------------------------------- калькулятор
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos,
          "log": math.log, "exp": math.exp, "min": min, "max": max, "floor": math.floor, "ceil": math.ceil}
_CONST = {"pi": math.pi, "e": math.e}


def safe_eval(expr):
    """Вычислить арифметическое выражение без eval (разбор через ast)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise BadArgument("Слишком большая степень")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _CONST:
            return _CONST[node.id]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                and not node.keywords):
            return _FUNCS[node.func.id](*(ev(x) for x in node.args))
        raise BadArgument("Недопустимое выражение (разрешены числа, + - * / // % **, скобки, "
                          "sqrt abs round sin cos log exp min max floor ceil, pi e)")
    try:
        return ev(ast.parse(expr.replace("^", "**"), mode="eval"))
    except SyntaxError:
        raise BadArgument(f"Синтаксическая ошибка в выражении «{expr}»") from None
    except ZeroDivisionError:
        raise BadArgument("Деление на ноль") from None
    except (ValueError, OverflowError, TypeError) as e:
        raise BadArgument(f"Ошибка вычисления: {e}") from None


class ActiveFunctions:
    # ================================================================ строки
    @active("string", usage="string слова...", group=A_STR, help="Склеить аргументы через пробел.")
    def af_string(self, a):
        return " ".join(a)

    @active("length", "ln", usage="length строка", group=A_STR, help="Длина строки.")
    def af_length(self, a):
        return str(len(" ".join(a)))

    @active("substr", usage="substr строка начало [длина]", group=A_STR, help="Подстрока (нумерация с 1).")
    def af_substr(self, a):
        need(a, 2, "substr строка начало [длина]")
        start = to_int(a[1], "начало", 1) - 1
        end = start + to_int(a[2], "длина") if len(a) > 2 else None
        return a[0][start:end]

    @active("index", usage="index строка подстрока", group=A_STR,
            help="Позиция первого вхождения подстроки (с 1), 0 — если нет.")
    def af_index(self, a):
        need(a, 2, "index строка подстрока")
        return str(a[0].find(a[1]) + 1)

    @active("reverse", "rv", usage="reverse строка", group=A_STR, help="Строка задом наперёд.")
    def af_reverse(self, a):
        return " ".join(a)[::-1]

    @active("upper_case", "uc", usage="upper_case строка", group=A_STR, help="В верхний регистр.")
    def af_upper(self, a):
        return " ".join(a).upper()

    @active("lower_case", "lc", usage="lower_case строка", group=A_STR, help="В нижний регистр.")
    def af_lower(self, a):
        return " ".join(a).lower()

    @active("before", "be", usage="before строка подстрока", group=A_STR, help="Часть строки до подстроки.")
    def af_before(self, a):
        need(a, 2, "before строка подстрока")
        return a[0].split(a[1], 1)[0]

    @active("after", "af", usage="after строка подстрока", group=A_STR, help="Часть строки после подстроки.")
    def af_after(self, a):
        need(a, 2, "after строка подстрока")
        return a[0].split(a[1], 1)[1] if a[1] in a[0] else ""

    @active("ltrim", usage="ltrim строка", group=A_STR, help="Убрать пробелы слева.")
    def af_ltrim(self, a):
        return a[0].lstrip() if a else ""

    @active("rtrim", usage="rtrim строка", group=A_STR, help="Убрать пробелы справа.")
    def af_rtrim(self, a):
        return a[0].rstrip() if a else ""

    @active("format_line", "fl", usage="format_line управляющая_строка аргументы...", group=A_STR,
            help="Форматирование в стиле ioa_: ^a — строка, ^d — число, ^f — дробное, ^/ — перевод строки, ^^ — '^'.")
    def af_format_line(self, a):
        need(a, 1, "format_line управляющая_строка аргументы...")
        ctl, args, out, i = a[0], list(a[1:]), [], 0
        while i < len(ctl):
            if ctl[i] == "^" and i + 1 < len(ctl):
                code = ctl[i + 1]
                if code in "adf":
                    if not args:
                        raise BadArgument("format_line: не хватает аргументов")
                    v = args.pop(0)
                    out.append(fmt_number(parse_number(v)) if code == "d" else
                               f"{float(parse_number(v)):.2f}" if code == "f" else v)
                elif code == "/":
                    out.append("\n")
                elif code == "^":
                    out.append("^")
                else:
                    out.append(ctl[i:i + 2])
                i += 2
                continue
            out.append(ctl[i])
            i += 1
        return "".join(out)

    @active("unique", group=A_STR, help="Уникальное имя (как !BBBxxxxx в Multics).")
    def af_unique(self, a):
        return "!BBB" + uuid.uuid4().hex[:8]

    @active("nothing", group=A_STR, help="Пустая строка.")
    def af_nothing(self, a):
        return ""

    # ============================================================ арифметика
    def _nums(self, a, n=1):
        need(a, n, "числа...")
        return [parse_number(x) for x in a]

    @active("plus", usage="plus числа...", group=A_ARITH, help="Сумма.")
    def af_plus(self, a):
        return fmt_number(sum(self._nums(a)))

    @active("minus", usage="minus a [b...]", group=A_ARITH, help="Разность (a - b - ...) или смена знака.")
    def af_minus(self, a):
        n = self._nums(a)
        return fmt_number(-n[0] if len(n) == 1 else n[0] - sum(n[1:]))

    @active("times", usage="times числа...", group=A_ARITH, help="Произведение.")
    def af_times(self, a):
        return fmt_number(math.prod(self._nums(a)))

    @active("divide", usage="divide a b", group=A_ARITH, help="Деление.")
    def af_divide(self, a):
        x, y = self._nums(a, 2)[:2]
        if y == 0:
            raise BadArgument("Деление на ноль")
        return fmt_number(x / y)

    @active("quotient", usage="quotient a b", group=A_ARITH, help="Целая часть от деления.")
    def af_quotient(self, a):
        x, y = self._nums(a, 2)[:2]
        if y == 0:
            raise BadArgument("Деление на ноль")
        return fmt_number(int(x / y))

    @active("mod", usage="mod a b", group=A_ARITH, help="Остаток от деления.")
    def af_mod(self, a):
        x, y = self._nums(a, 2)[:2]
        if y == 0:
            raise BadArgument("Деление на ноль")
        return fmt_number(x % y)

    @active("max", usage="max числа...", group=A_ARITH, help="Максимум.")
    def af_max(self, a):
        return fmt_number(max(self._nums(a)))

    @active("min", usage="min числа...", group=A_ARITH, help="Минимум.")
    def af_min(self, a):
        return fmt_number(min(self._nums(a)))

    @active("trunc", usage="trunc число", group=A_ARITH, help="Отбросить дробную часть.")
    def af_trunc(self, a):
        return str(math.trunc(self._nums(a)[0]))

    @active("floor", usage="floor число", group=A_ARITH, help="Округление вниз.")
    def af_floor(self, a):
        return str(math.floor(self._nums(a)[0]))

    @active("ceil", usage="ceil число", group=A_ARITH, help="Округление вверх.")
    def af_ceil(self, a):
        return str(math.ceil(self._nums(a)[0]))

    @active("calc", usage="calc выражение", group=A_ARITH,
            help="Калькулятор: calc (2+3)*4, calc sqrt(2), calc 2^10.")
    def af_calc(self, a):
        need(a, 1, "calc выражение")
        return fmt_number(safe_eval(" ".join(a)))

    # ================================================================ логика
    @active("true", group=A_LOGIC, help="Возвращает true.")
    def af_true(self, a):
        return TRUE

    @active("false", group=A_LOGIC, help="Возвращает false.")
    def af_false(self, a):
        return FALSE

    @active("equal", usage="equal a b", group=A_LOGIC, help="true, если строки равны.")
    def af_equal(self, a):
        need(a, 2, "equal a b")
        return from_bool(a[0] == a[1])

    @active("nequal", usage="nequal a b", group=A_LOGIC, help="true, если числа равны.")
    def af_nequal(self, a):
        x, y = self._nums(a, 2)[:2]
        return from_bool(x == y)

    @active("less", usage="less a b", group=A_LOGIC, help="Строки: a < b.")
    def af_less(self, a):
        need(a, 2, "less a b")
        return from_bool(a[0] < a[1])

    @active("greater", usage="greater a b", group=A_LOGIC, help="Строки: a > b.")
    def af_greater(self, a):
        need(a, 2, "greater a b")
        return from_bool(a[0] > a[1])

    @active("nless", usage="nless a b", group=A_LOGIC, help="Числа: a < b.")
    def af_nless(self, a):
        x, y = self._nums(a, 2)[:2]
        return from_bool(x < y)

    @active("ngreater", usage="ngreater a b", group=A_LOGIC, help="Числа: a > b.")
    def af_ngreater(self, a):
        x, y = self._nums(a, 2)[:2]
        return from_bool(x > y)

    @active("not", usage="not true|false", group=A_LOGIC, help="Логическое НЕ.")
    def af_not(self, a):
        need(a, 1, "not true|false")
        return from_bool(not to_bool(a[0]))

    @active("and", usage="and значения...", group=A_LOGIC, help="Логическое И.")
    def af_and(self, a):
        return from_bool(all(to_bool(x) for x in a))

    @active("or", usage="or значения...", group=A_LOGIC, help="Логическое ИЛИ.")
    def af_or(self, a):
        return from_bool(any(to_bool(x) for x in a))

    @active("if", usage="if true|false -then A [-else B]", group=A_LOGIC,
            help="Условное значение. Как команда: if [условие] -then \"команда\" -else \"команда\".")
    def af_if(self, a):
        return self._if_branch(a)

    def _if_branch(self, a):
        need(a, 3, "if условие -then A [-else B]")
        if a[1] != "-then":
            raise BadArgument("После условия ожидается -then")
        then_part, else_part = a[2], ""
        if len(a) > 3:
            if a[3] != "-else" or len(a) < 5:
                raise BadArgument("Ожидается -else значение")
            else_part = a[4]
        return then_part if to_bool(a[0]) else else_part

    @command("if", usage='if условие -then "команда" [-else "команда"]', group=G_SHELL,
             help="Выполнить одну из команд в зависимости от условия (true/false).")
    def cmd_if(self, a):
        line = self._if_branch(a)
        if line:
            self.execute(line)

    @active("exists", usage="exists [entry|segment|directory|link] путь", login=True, group=A_LOGIC,
            help="true, если объект существует (и имеет нужный тип).")
    def af_exists(self, a):
        need(a, 1, "exists [тип] путь")
        kind, path = ("entry", a[0]) if len(a) == 1 else (a[0], a[1])
        if kind not in ("entry", "segment", "directory", "link", "seg", "dir"):
            raise BadArgument("Тип: entry, segment, directory или link")
        chase = kind in ("segment", "seg", "directory", "dir")   # для entry/link — сама запись
        try:
            seg = self.system.fs.resolve(path, self.proc.wdir, chase=chase)
        except MulticsError:
            return FALSE
        ok = {"entry": True, "segment": not seg.is_dir and not seg.is_link, "seg": not seg.is_dir and not seg.is_link,
              "directory": seg.is_dir, "dir": seg.is_dir, "link": seg.is_link}[kind]
        return from_bool(ok)

    # ========================================================= пути и файлы
    @active("working_dir", "wd", login=True, group=A_PATH, help="Рабочий каталог.")
    def af_wd(self, a):
        return self.proc.wdir.path

    @active("home_dir", "hd", login=True, group=A_PATH, help="Домашний каталог.")
    def af_hd(self, a):
        p = self.proc.principal
        return f">udd>{p.project}>{p.person}"

    @active("process_dir", "pd", login=True, group=A_PATH, help="Каталог процесса в >pdd (удаляется при выходе).")
    def af_pd(self, a):
        return self.proc.pdir.path if self.proc.pdir else ">pdd"

    @active("default_wdir", "dwd", login=True, group=A_PATH, help="Каталог по умолчанию.")
    def af_dwd(self, a):
        return self.proc.default_wdir.path

    @active("path", usage="path относительный_путь", login=True, group=A_PATH, help="Абсолютный путь объекта.")
    def af_path(self, a):
        need(a, 1, "path путь")
        return self.system.fs.resolve(a[0], self.proc.wdir, chase=False).path

    @active("directory", usage="directory путь", login=True, group=A_PATH, help="Каталог, содержащий объект.")
    def af_directory(self, a):
        need(a, 1, "directory путь")
        return self.system.fs.split_parent(a[0], self.proc.wdir)[0].path

    @active("entry", usage="entry путь", group=A_PATH, help="Последний компонент пути (имя записи).")
    def af_entry(self, a):
        need(a, 1, "entry путь")
        return a[0].replace("<", ">").split(">")[-1]

    @active("suffix", usage="suffix имя", group=A_PATH, help="Суффикс имени после последней точки.")
    def af_suffix(self, a):
        need(a, 1, "suffix имя")
        name = a[0].split(">")[-1]
        return name.rsplit(".", 1)[1] if "." in name else ""

    @active("strip", usage="strip имя [суффикс]", group=A_PATH, help="Имя без суффикса (strip x.txt txt -> x).")
    def af_strip(self, a):
        need(a, 1, "strip имя [суффикс]")
        if len(a) > 1:
            return a[0][: -len(a[1]) - 1] if a[0].endswith("." + a[1]) else a[0]
        return a[0].rsplit(".", 1)[0]

    @active("contents", usage="contents путь", login=True, group=A_PATH,
            help="Содержимое сегмента (строки через пробел).")
    def af_contents(self, a):
        need(a, 1, "contents путь")
        seg = self._resolve(a[0])
        self.system.reference(self.proc, seg, "r")
        return " ".join(seg.content.split("\n"))

    def _names(self, a, pred):
        pattern = a[0] if a else "**"          # по умолчанию — все имена; обычное имя тоже допустимо
        items = self.system.fs.match(self.proc.subject, self.proc.wdir, pattern)
        return " ".join(s.name for s in items if pred(s))

    @active("segments", "segs", usage="segments [звёздное_имя]", login=True, group=A_PATH, help="Имена сегментов.")
    def af_segments(self, a):
        return self._names(a, lambda s: not s.is_dir and not s.is_link)

    @active("directories", "dirs", usage="directories [звёздное_имя]", login=True, group=A_PATH, help="Имена каталогов.")
    def af_directories(self, a):
        return self._names(a, lambda s: s.is_dir)

    @active("files", usage="files [звёздное_имя]", login=True, group=A_PATH, help="Имена сегментов и каталогов.")
    def af_files(self, a):
        return self._names(a, lambda s: not s.is_link)

    @active("entries", usage="entries [звёздное_имя]", login=True, group=A_PATH, help="Все записи, включая ссылки.")
    def af_entries(self, a):
        return self._names(a, lambda s: True)

    # ======================================================= время и прочее
    @active("date", group=A_INFO, help="Дата (ДД.ММ.ГГГГ).")
    def af_date(self, a):
        return f"{datetime.now():%d.%m.%Y}"

    @active("time", group=A_INFO, help="Время (ЧЧ:ММ:СС).")
    def af_time(self, a):
        return f"{datetime.now():%H:%M:%S}"

    @active("date_time", "dt", group=A_INFO, help="Дата и время.")
    def af_date_time(self, a):
        return f"{datetime.now():%d.%m.%Y %H:%M:%S}"

    @active("day_name", group=A_INFO, help="День недели.")
    def af_day_name(self, a):
        return DAYS[datetime.now().weekday()]

    @active("clock", group=A_INFO, help="Виртуальное время планировщика (такты).")
    def af_clock(self, a):
        return str(self.system.scheduler.clock)

    @active("user", usage="user [name|person|project|tag|pid|ring|login_time]", login=True, group=A_INFO,
            help="Сведения о текущем пользователе и процессе.")
    def af_user(self, a):
        p = self.proc
        key = a[0] if a else "name"
        values = {"name": f"{p.principal.person}.{p.principal.project}", "person": p.principal.person,
                  "project": p.principal.project, "tag": p.principal.tag, "pid": str(p.pid),
                  "ring": str(p.ring), "login_time": f"{p.login_time:%H:%M:%S}"}
        if key not in values:
            raise BadArgument(f"Ключ: {', '.join(values)}")
        return values[key]

    # ============================================================ переменные
    def _values(self):
        return self.system.values.setdefault(self.system.key(self.proc.principal), {})

    @active("value_get", "vg", usage="value_get имя", login=True, group=A_INFO,
            help="Значение переменной пользователя (value_set).")
    def af_value_get(self, a):
        need(a, 1, "value_get имя")
        values = self._values()
        if a[0] not in values:
            raise NotFound(f"Переменная «{a[0]}» не задана (value_set)")
        return values[a[0]]

    @active("value_defined", "vdf", usage="value_defined имя", login=True, group=A_INFO,
            help="true, если переменная задана.")
    def af_value_defined(self, a):
        need(a, 1, "value_defined имя")
        return from_bool(a[0] in self._values())

    @command("value_set", "vs", usage="value_set имя значение", group=G_SHELL,
             help="Задать переменную пользователя (сохраняется между сеансами).")
    def cmd_value_set(self, a):
        need(a, 2, "value_set имя значение")
        self._values()[a[0]] = " ".join(a[1:])
        self.out(f"{a[0]} = {self._values()[a[0]]}")

    @command("value_list", "vls", group=G_SHELL, help="Список переменных пользователя.")
    def cmd_value_list(self, a):
        values = self._values()
        if not values:
            self.out("Переменных нет.")
        for k in sorted(values):
            self.out(f"  {k} = {values[k]}")

    @command("value_delete", "vdl", usage="value_delete имя", group=G_SHELL, help="Удалить переменную.")
    def cmd_value_delete(self, a):
        need(a, 1, "value_delete имя")
        if self._values().pop(a[0], None) is None:
            raise NotFound(f"Переменная «{a[0]}» не задана")
        self.out(f"Переменная {a[0]} удалена")
