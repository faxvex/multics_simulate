"""Ядро системы: связывает файловую систему, пользователей, память,
планировщик, компоновщик и процессы. Здесь же начальная загрузка (bootstrap)
и общесистемные службы: каталоги процессов, сообщения, почта, переменные
(value), сокращения (abbrev), администрирование и демон печати (dprint)."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .access import Subject, check_access
from .acl import Principal
from .errors import AccessViolation, BadArgument, MulticsError, NotFound
from .filesystem import FileSystem
from .interpreter import Interpreter
from .linker import Linker
from .memory import VirtualMemory
from .process import Process, Task
from .rings import KERNEL_RING, USER_RING, RingBrackets
from .scheduler import TrafficController
from .users import UserRegistry

PAGE_SIZE = 64   # символов на страницу (маленькая, чтобы страничные прерывания были видны)
N_FRAMES = 4     # физических кадров

# (Person, Project, пароль) — учётные записи, создаваемые при загрузке
INITIAL_USERS = [
    ("Admin", "SysAdmin", "admin"),
    ("Ivanov", "Students", "ivan"),
    ("Petrov", "Students", "petr"),
    ("Guest", "Visitors", "guest"),
]

MOTD = "Добро пожаловать в учебный Multics! Справка: help. Новое: help -af (активные функции)."


@dataclass
class PrintRequest:
    """Запрос к демону печати (dprint)."""
    id: int
    path: str
    user: str
    content: str


class System:
    def __init__(self, out=print):
        self.out = out
        self.fs = FileSystem(PAGE_SIZE)
        self.users = UserRegistry()
        self.memory = VirtualMemory(N_FRAMES, PAGE_SIZE, out)
        self.scheduler = TrafficController(self)
        self.linker = Linker(self)
        self.interp = Interpreter(self)
        self.processes = {}
        self._next_pid = 0
        # общесистемные службы; ключ — (person, project)
        self.messages = {}     # мгновенные сообщения (send_message)
        self.mail = {}         # почтовые ящики (send_mail)
        self.values = {}       # переменные пользователя (value_set / value_get)
        self.abbrevs = {}      # сокращения (abbrev)
        self.print_queue = deque()
        self._next_request = 1
        self.io_daemon = None
        self.kernel = self._spawn(Principal("Initializer", "SysControl", "z"), KERNEL_RING, self.fs.root)
        self.fs.before_delete.append(self._check_not_in_use)
        self.fs.on_delete.append(self._segment_deleted)
        self._bootstrap()

    # ---------------------------------------------------------- процессы
    def _spawn(self, principal, ring, wdir):
        proc = Process(self._next_pid, principal, ring, wdir, self.out)
        self._next_pid += 1
        self.processes[proc.pid] = proc
        return proc

    @staticmethod
    def key(principal):
        return principal.person, principal.project

    def logged_in(self, person, project):
        return next((p for p in self.processes.values()
                     if (p.principal.person, p.principal.project) == (person, project)
                     and p.principal.tag == "a"), None)

    def login(self, person, project, password):
        if not self.users.check(person, project, password):
            hint = ""
            if (person, project) not in self.users.all():      # частая ошибка: ivanov вместо Ivanov
                same = [f"{p}.{j}" for p, j in self.users.all()
                        if (p.lower(), j.lower()) == (person.lower(), project.lower())]
                if same:
                    hint = f" (регистр букв важен: {same[0]})"
            raise AccessViolation(f"Неверное имя пользователя, проект или пароль{hint}")
        if self.logged_in(person, project):
            raise MulticsError(f"{person}.{project} уже работает в системе")
        return self._create_user_process(person, project, greet=True)

    def _create_user_process(self, person, project, greet):
        try:
            home = self.fs.resolve(f">udd>{project}>{person}", self.fs.root)
        except NotFound:
            home = self.fs.root
        proc = self._spawn(Principal(person, project, "a"), USER_RING, home)
        proc.pdir = self._make_pdir(proc)
        self.out(f"Создан процесс pid {proc.pid} для {proc.principal}, кольцо {proc.ring}, "
                 f"рабочий каталог {home.path}")
        if greet:
            motd = self.fs.root.children["sss"].children.get("motd")
            if motd is not None and motd.content:
                self.out(f"*** {motd.content}")
            unread = sum(1 for m in self.mail.get((person, project), []) if not m["read"])
            if unread:
                self.out(f"У вас новых писем: {unread} (read_mail)")
        return proc

    def _make_pdir(self, proc):
        k = self.kernel.subject
        pdir = self.fs.create(k, self.fs.root, f">pdd>!pid_{proc.pid}", is_dir=True)
        pdir.acl.entries.clear()
        pdir.acl.set(f"{proc.principal.person}.{proc.principal.project}.*", "sma", True)
        pdir.brackets = RingBrackets(USER_RING, 7, 7)
        pdir.owner = str(proc.principal)
        return pdir

    def logout(self, proc):
        self.scheduler.forget(proc.pid)
        if proc.tasks:
            self.out(f"Снято незавершённых задач процесса {proc.pid}: {len(proc.tasks)}")
            for task in proc.tasks:
                task.gen.close()
            proc.tasks.clear()
        del self.processes[proc.pid]
        if proc.pdir is not None and proc.pdir.parent is not None:
            try:
                self.fs.delete(self.kernel.subject, self.fs.root, proc.pdir.path, True, force=True)
            except MulticsError as e:
                self.out(f"  (каталог процесса {proc.pdir.path} не удалён: {e})")
        self.out(f"Процесс pid {proc.pid} ({proc.principal}) завершён, KST уничтожена "
                 f"(известных сегментов: {len(proc.kst.entries)})")

    def new_proc(self, proc):
        """Команда new_proc: уничтожить процесс и создать новый для того же пользователя."""
        self.logout(proc)
        return self._create_user_process(proc.principal.person, proc.principal.project, greet=False)

    def is_admin(self, proc):
        return proc.principal.project == "SysAdmin" or proc.pid == 0

    def require_admin(self, proc):
        if not self.is_admin(proc):
            raise AccessViolation("Команда доступна только администраторам (проект SysAdmin)")

    def bump(self, actor, pid):
        """Принудительно завершить процесс (свой — любому, чужой — администратору)."""
        target = self.processes.get(pid)
        if target is None or pid == 0:
            raise NotFound(f"Нет пользовательского процесса pid {pid}")
        if target.principal != actor.principal and not self.is_admin(actor):
            raise AccessViolation("Завершать чужие процессы может только администратор")
        if target is self.io_daemon:
            self.io_daemon = None
        self.logout(target)
        return target

    # --------------------------------------------------- доступ к сегментам
    def reference(self, proc, seg, mode, offset=None, touch=True):
        """Единая точка обращения процесса к сегменту: проверка ACL+кольцо,
        инициация в KST, обращение к страницам виртуальной памяти.
        offset=None — затронуть все страницы сегмента."""
        ring = check_access(proc.subject, seg, mode)
        if proc.kst.initiate(seg, proc.ring):
            self.out(f"  [ядро] сегмент #{seg.segno} ({seg.name}) стал известен процессу {proc.pid} (KST)")
        if touch and not seg.is_dir:
            if offset is None:
                offsets = [p * PAGE_SIZE for p in range(self.memory.pages_of(seg))]
            else:
                if offset >= max(seg.size, 1):
                    raise BadArgument(f"Смещение {offset} вне сегмента {seg.path} (размер {seg.size})")
                offsets = [offset]
            for off in offsets:
                self.memory.access(seg, off, mode == "w", proc)
        return ring

    def set_content(self, proc, seg, text):
        """Записать содержимое сегмента (с обращениями к памяти на запись)."""
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог")
        check_access(proc.subject, seg, "w")
        self.fs.check_quota(seg.parent, -(-len(text) // PAGE_SIZE) - self.fs.pages(seg))
        old_pages = self.memory.pages_of(seg)
        seg.content = text
        seg.modified = datetime.now()
        new_pages = self.memory.pages_of(seg)
        if new_pages < old_pages:
            self.memory.drop_segment(seg.segno, new_pages)
        self.reference(proc, seg, "w")

    def _check_not_in_use(self, seg):
        """Нельзя удалить каталог, который является рабочим каталогом процесса."""
        for p in self.processes.values():
            if p.wdir is seg:
                raise MulticsError(f"Каталог {seg.path} — рабочий каталог процесса {p.pid} ({p.principal})")

    def _segment_deleted(self, seg):
        self.memory.drop_segment(seg.segno)
        for proc in self.processes.values():
            proc.kst.terminate(seg.segno)
            for key in [k for k, l in proc.linkage.items() if l.segment is seg or k[0] == seg.segno]:
                del proc.linkage[key]
            if proc.default_wdir is seg:
                proc.default_wdir = self.fs.root

    # ------------------------------------------------- сообщения и почта
    def find_user(self, ident):
        """'Person' или 'Person.Project' -> (person, project)."""
        parts = ident.split(".")
        if len(parts) >= 2:
            if not self.users.exists(parts[0], parts[1]):
                raise NotFound(f"Нет пользователя {parts[0]}.{parts[1]}")
            return parts[0], parts[1]
        projects = self.users.projects_of(parts[0])
        if not projects:
            raise NotFound(f"Нет пользователя {parts[0]}")
        if len(projects) > 1:
            raise BadArgument(f"{parts[0]} есть в нескольких проектах, укажите Person.Project")
        return parts[0], projects[0]

    def send_message(self, sender, ident, text):
        person, project = self.find_user(ident)
        self.messages.setdefault((person, project), []).append(
            (str(sender.principal), text, datetime.now()))
        target = self.logged_in(person, project)
        if target is None:
            return f"{person}.{project} не в системе — сообщение будет показано при входе"
        if not target.accept_messages:
            return f"{person}.{project} отложил сообщения (defer_messages) — сообщение сохранено"
        return f"Сообщение для {person}.{project} доставлено"

    def take_messages(self, principal):
        return self.messages.pop(self.key(principal), [])

    def send_mail(self, sender, ident, text):
        person, project = self.find_user(ident)
        self.mail.setdefault((person, project), []).append(
            {"from": str(sender.principal), "text": text, "time": datetime.now(), "read": False})
        return person, project

    # ------------------------------------------------------- администрирование
    def new_user(self, admin, person, project, password):
        self.require_admin(admin)
        self.users.register(person, project, password)
        self._make_home(person, project)

    def delete_user(self, admin, person, project):
        self.require_admin(admin)
        if self.logged_in(person, project):
            raise MulticsError(f"{person}.{project} сейчас в системе — сначала bump")
        self.users.remove(person, project)

    def _make_home(self, person, project):
        k = self.kernel.subject
        udd = self.fs.root.children["udd"]
        if project not in udd.children:
            proj = self.fs.create(k, udd, project, is_dir=True)
            proj.acl.set("*.*.*", "s", True)
        proj = udd.children[project]
        if person in proj.children:
            return proj.children[person]
        home = self.fs.create(k, proj, person, is_dir=True)
        home.acl.entries.clear()
        home.acl.set(f"{person}.{project}.*", "sma", True)
        home.acl.set(f"*.{project}.*", "s", True)
        home.acl.set("Admin.SysAdmin.*", "sma", True)
        home.brackets = RingBrackets(USER_RING, 7, 7)   # пользователь (кольцо 4) может менять
        home.owner = f"{person}.{project}.a"
        return home

    # ------------------------------------------------------- демон печати
    def dprint(self, proc, seg, copies=1):
        if seg.is_dir:
            raise BadArgument(f"{seg.path} — каталог")
        self.reference(proc, seg, "r")
        ids = []
        for _ in range(copies):
            req = PrintRequest(self._next_request, seg.path, str(proc.principal), seg.content)
            self._next_request += 1
            self.print_queue.append(req)
            ids.append(req.id)
        daemon = self._get_io_daemon()
        if not daemon.tasks:
            daemon.tasks.append(Task("печать", self._daemon_loop(), daemon.ring))
            self.scheduler.enqueue(daemon)
        return ids

    def _get_io_daemon(self):
        if self.io_daemon is None or self.io_daemon.pid not in self.processes:
            self.io_daemon = self._spawn(Principal("IO", "SysDaemon", "z"), USER_RING, self.fs.root)
            self.out(f"  [ядро] запущен демон печати IO.SysDaemon (pid {self.io_daemon.pid})")
        return self.io_daemon

    def _daemon_loop(self):
        """Задача демона: печатает по одной строке за такт, пока очередь не пуста."""
        while self.print_queue:
            req = self.print_queue.popleft()
            lines = req.content.split("\n") if req.content else ["(пусто)"]
            self.out(f"[IO daemon] печать запроса №{req.id}: {req.path} (для {req.user})")
            for line in lines:
                self.out(f"  | {line}")
                yield
            self.out(f"[IO daemon] запрос №{req.id} напечатан, строк: {len(lines)}")

    def cancel_print(self, proc, req_id):
        for req in self.print_queue:
            if req.id == req_id:
                if req.user != str(proc.principal) and not self.is_admin(proc):
                    raise AccessViolation("Отменять чужие запросы может только администратор")
                self.print_queue.remove(req)
                return req
        raise NotFound(f"Нет запроса №{req_id} в очереди печати")

    # ------------------------------------------------- начальная загрузка
    def _bootstrap(self):
        k = Subject(self.kernel.principal, KERNEL_RING)
        fs, root = self.fs, self.fs.root
        root.acl.set("*.*.*", "s", True)
        root.acl.set("Initializer.SysControl.*", "sma", True)
        for name in ("udd", "sss", "pdd"):
            d = fs.create(k, root, name, is_dir=True)
            d.acl.set("*.*.*", "s", True)
            d.brackets = RingBrackets(0, 7, 7)
        for person, project, password in INITIAL_USERS:
            self.users.register(person, project, password)
            self._make_home(person, project)

        sss = root.children["sss"]

        def system_seg(name, content, brackets, modes, executable=True, gate=False):
            seg = fs.create(k, sss, name, content=content, executable=executable, gate=gate)
            seg.acl.entries.clear()
            seg.acl.set("*.*.*", modes, False)
            seg.brackets = RingBrackets(*brackets)
            return seg

        system_seg("sss_hello", "echo [sss_hello] системная библиотека приветствует вас", (1, 5, 5), "re")
        system_seg("kernel_data",
                   "Таблицы ядра: AST, SST, дескрипторы сегментов. Только для кольца 0.",
                   (0, 0, 0), "r", executable=False)
        system_seg("hcs_",
                   "echo [hcs_] выполняется в ядре через шлюз\n"
                   "ring\n"
                   "read >sss>kernel_data\n"
                   "echo [hcs_] таблицы ядра прочитаны, возвращаюсь в кольцо вызывающего",
                   (0, 0, 5), "re", gate=True)
        motd = system_seg("motd", MOTD, (4, 4, 4), "r", executable=False)
        motd.acl.set("Admin.SysAdmin.*", "rw", False)
        for seg in sss.children.values():
            seg.safety = True                      # системные сегменты защищены от удаления
