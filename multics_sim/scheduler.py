"""Traffic controller — планировщик процессов по кругу (round-robin).

Каждому готовому процессу выделяется квант из QUANTUM инструкций; затем процесс
уходит в конец очереди. Время — виртуальное: один такт = одна инструкция.
При переключении на задачу восстанавливается её кольцо, а после кванта
процессу возвращается кольцо командной оболочки (контекст сохраняется).
"""
from collections import deque

from .errors import MulticsError


class TrafficController:
    QUANTUM = 2

    def __init__(self, system):
        self.system = system
        self.queue = deque()   # pid готовых процессов
        self.clock = 0

    def enqueue(self, proc):
        if proc.pid not in self.queue:
            self.queue.append(proc.pid)

    def forget(self, pid):
        if pid in self.queue:
            self.queue.remove(pid)

    def _step(self, proc, task):
        """Одна инструкция задачи. Возвращает False, если задача закончилась."""
        out = self.system.out
        shell_ring, proc.ring = proc.ring, task.ring        # загрузить контекст задачи
        try:
            next(task.gen)
            proc.cpu_ticks += 1
            self.clock += 1
            return True
        except StopIteration:
            out(f"[traffic controller] pid {proc.pid}: задача «{task.name}» завершена")
        except MulticsError as e:
            out(f"[traffic controller] pid {proc.pid}: задача «{task.name}» аварийно завершена: {e}")
        finally:
            task.ring, proc.ring = proc.ring, shell_ring    # сохранить контекст задачи
        proc.tasks.popleft()
        return False

    def run(self, max_quanta=None):
        """Выполнять кванты, пока есть готовые задачи (или не исчерпан лимит)."""
        out, done = self.system.out, 0
        while self.queue and (max_quanta is None or done < max_quanta):
            proc = self.system.processes.get(self.queue.popleft())
            if proc is None or not proc.tasks:
                continue
            done += 1
            out(f"[traffic controller] t={self.clock:03d}: квант -> pid {proc.pid} ({proc.principal.person})")
            for _ in range(self.QUANTUM):
                if not self._step(proc, proc.tasks[0]) or not proc.tasks:
                    break
            if proc.tasks:
                self.queue.append(proc.pid)    # в конец очереди
        if not self.queue:
            out("[traffic controller] очередь готовых процессов пуста")
        return done
