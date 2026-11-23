"""Общая инфраструктура команд: декораторы регистрации, разбор командной
строки по правилам Multics, числа и логические значения активных функций."""
from dataclasses import dataclass

from ..errors import BadArgument

# Группы для справки
G_FS = "Файловая система"
G_ACL = "Доступ: ACL, кольца, защита"
G_USER = "Пользователи и связь"
G_ADMIN = "Администрирование"
G_PROC = "Процессы, связывание, планировщик"
G_MEM = "Виртуальная память"
G_SHELL = "Командная среда"

A_STR = "Строки"
A_ARITH = "Арифметика"
A_LOGIC = "Логика и условия"
A_PATH = "Пути и файлы"
A_INFO = "Время, пользователь, переменные"

TRUE, FALSE = "true", "false"


@dataclass
class Meta:
    name: str
    aliases: tuple
    usage: str
    help: str
    login: bool
    group: str
    kind: str      # "command" или "active"


def command(name, *aliases, usage="", help="", login=True, group=G_SHELL):
    """Регистрирует метод как команду оболочки: fn(self, args) -> None."""
    def deco(fn):
        fn.cmd_meta = Meta(name, aliases, usage or name, help, login, group, "command")
        return fn
    return deco


def active(name, *aliases, usage="", help="", login=False, group=A_STR):
    """Регистрирует метод как активную функцию: fn(self, args) -> str.
    Активная функция вызывается в квадратных скобках: echo [plus 2 3]
    и может быть вызвана как команда — тогда результат печатается."""
    def deco(fn):
        fn.af_meta = Meta(name, aliases, usage or name, help, login, group, "active")
        return fn
    return deco


def need(args, n, usage):
    if len(args) < n:
        raise BadArgument(f"Использование: {usage}")


# --------------------------------------------------------------- разбор строки
def tokenize(line):
    """Разбить строку на аргументы. Кавычки — только двойные; "" внутри
    кавычек означает саму кавычку (соглашение Multics)."""
    tokens, cur, in_tok, i = [], [], False, 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_tok, i = True, i + 1
            while True:
                if i >= len(line):
                    raise BadArgument("Незакрытая кавычка")
                if line[i] == '"':
                    if line[i + 1:i + 2] == '"':
                        cur.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                cur.append(line[i])
                i += 1
            continue
        if c.isspace():
            if in_tok:
                tokens.append("".join(cur))
                cur, in_tok = [], False
            i += 1
            continue
        cur.append(c)
        in_tok = True
        i += 1
    if in_tok:
        tokens.append("".join(cur))
    return tokens


def quote_token(value):
    return '"' + value.replace('"', '""') + '"'


def matching_bracket(line, start):
    """Индекс ']' парной к '[' в позиции start (с учётом вложенности и кавычек)."""
    depth, quote = 0, False
    for i in range(start, len(line)):
        c = line[i]
        if c == '"':
            quote = not quote
        elif not quote and c == "[":
            depth += 1
        elif not quote and c == "]":
            depth -= 1
            if depth == 0:
                return i
    raise BadArgument("Нет закрывающей скобки ']' у активной функции")


# --------------------------------------------------------------- значения
def parse_number(text):
    try:
        return float(text) if any(c in text for c in ".eE") else int(text)
    except ValueError:
        raise BadArgument(f"«{text}» — не число") from None


def fmt_number(x):
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return str(round(x, 10)) if isinstance(x, float) else str(x)


def to_bool(text):
    if text == TRUE:
        return True
    if text == FALSE:
        return False
    raise BadArgument(f"Ожидалось true или false, получено «{text}»")


def from_bool(value):
    return TRUE if value else FALSE
