"""exec_com — интерпретатор командных сценариев Multics.

Сценарий — сегмент (обычно с суффиксом .ec), каждая строка которого —
команда оболочки или управляющая директива:
  &1 .. &9      аргументы сценария        &n   число аргументов
  &f1 .. &f9    аргументы начиная с N     &&   символ '&'
  &- текст      комментарий
  &label имя    метка                     &goto имя   переход
  &if [условие] &then действие [&else действие]
  &print текст  вывести текст (активные функции вычисляются)
  &quit         завершить сценарий
  &command_line on|off   показывать ли выполняемые команды (по умолчанию on)
"""
import re

from .commands.base import to_bool
from .errors import BadArgument, MulticsError

MAX_STEPS = 10000
_ARG = re.compile(r"&&|&n\b|&f([1-9])|&([1-9])")


class ExecCom:
    def __init__(self, shell, seg, args):
        self.shell, self.seg, self.args = shell, seg, args
        self.show = True

    def substitute(self, line):
        def repl(m):
            text = m.group(0)
            if text == "&&":
                return "&"
            if text.startswith("&n"):
                return str(len(self.args))
            if m.group(1):
                return " ".join(self.args[int(m.group(1)) - 1:])
            i = int(m.group(2))
            return self.args[i - 1] if i <= len(self.args) else ""
        return _ARG.sub(repl, line)

    def run(self):
        lines = [l.strip() for l in self.seg.content.split("\n")]
        labels = {}
        for i, l in enumerate(lines):
            if l.startswith("&label "):
                labels[l.split()[1]] = i
        pc, steps = 0, 0
        while pc < len(lines):
            steps += 1
            if steps > MAX_STEPS:
                raise MulticsError(f"exec_com: превышено {MAX_STEPS} шагов (бесконечный цикл?)")
            line = lines[pc]
            pc += 1
            if not line or line.startswith("&-") or line.startswith("&label"):
                continue
            action = self.dispatch(self.substitute(line))
            if action == "quit":
                return
            if action is not None:                   # переход по метке
                if action not in labels:
                    raise BadArgument(f"exec_com: нет метки «{action}»")
                pc = labels[action] + 1
            if self.shell.proc is None and not self.shell.running:
                return

    def dispatch(self, line):
        """Выполнить одну строку. Возвращает 'quit', имя метки для перехода или None."""
        sh = self.shell
        if line.startswith("&goto"):
            return line.split()[1] if len(line.split()) > 1 else ""
        if line.startswith("&quit"):
            return "quit"
        if line.startswith("&print"):
            sh.out(sh.expand(line[6:].strip()))
            return None
        if line.startswith("&command_line"):
            self.show = line.split()[-1] != "off"
            return None
        if line.startswith("&if"):
            m = re.match(r"&if\s+(.*?)\s+&then\s+(.*?)(?:\s+&else\s+(.*))?$", line)
            if not m:
                raise BadArgument("exec_com: синтаксис — &if [условие] &then действие [&else действие]")
            cond = to_bool(sh.expand(m.group(1)).strip())
            chosen = m.group(2) if cond else (m.group(3) or "")
            return self.dispatch(chosen.strip()) if chosen.strip() else None
        if self.show:
            sh.out(f"ec> {line}")
        sh.execute(line)
        return None
