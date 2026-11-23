"""Пользователи и связь: вход/выход, сообщения, почта, администрирование."""
from ..errors import BadArgument, NotFound, to_int
from .base import G_ADMIN, G_USER, command, need


class UserCommands:
    # ------------------------------------------------------- вход и выход
    @command("login", "l", "lg", usage="login Person.Project [пароль]", login=False, group=G_USER,
             help="Вход в систему: создаётся процесс в кольце 4 и каталог процесса в >pdd.")
    def cmd_login(self, a):
        need(a, 1, "login Person.Project [пароль]")
        parts = a[0].split(".")
        if len(parts) < 2:
            raise BadArgument("Нужен идентификатор вида Person.Project")
        password = a[1] if len(a) > 1 else self._ask_password()
        self.stack.append(self.system.login(parts[0], parts[1], password))

    def _ask_password(self):
        """Запросить пароль обычным вводом строки.

        getpass не годится: на Windows он читает клавиши напрямую из консоли
        (msvcrt) и во встроенных терминалах (IDE, Claude, Windows Terminal с
        перенаправлением) зависает или получает пустую строку. Пароль в
        историю команд не попадает: он вводится не как команда.
        """
        try:
            return self.input_fn("Пароль: ")
        except (EOFError, StopIteration):
            raise BadArgument("Пароль не введён")

    @command("logout", "lo", group=G_USER, help="Завершить текущий процесс (выход из системы).")
    def cmd_logout(self, a):
        self.system.logout(self.stack.pop())
        self._announce_current()

    def _announce_current(self):
        if self.proc:
            self.out(f"Текущим стал процесс pid {self.proc.pid} ({self.proc.principal})")

    @command("new_proc", group=G_USER,
             help="Уничтожить текущий процесс и создать новый: чистые KST, связи и кольцо 4.")
    def cmd_new_proc(self, a):
        old = self.stack.pop()
        new = self.system.new_proc(old)
        self.stack.append(new)

    @command("switch", "sw", usage="switch pid", group=G_USER, help="Переключить терминал на другой вошедший процесс.")
    def cmd_switch(self, a):
        need(a, 1, "switch pid")
        pid = to_int(a[0], "номер процесса")
        target = next((p for p in self.stack if p.pid == pid), None)
        if target is None:
            raise NotFound(f"Нет вошедшего процесса pid {pid}")
        self.stack.remove(target)
        self.stack.append(target)
        self.out(f"Терминал подключён к процессу pid {pid} ({target.principal})")

    @command("who", usage="who [-long]", login=False, group=G_USER, help="Кто работает в системе.")
    def cmd_who(self, a):
        users = [p for p in self.system.processes.values() if p.principal.tag == "a"]
        if not users:
            self.out("В системе никого нет.")
        for p in users:
            line = f"  {str(p.principal):<28} pid {p.pid}  кольцо {p.ring}"
            if "-long" in a or "-lg" in a:
                line += f"  вход {p.login_time:%H:%M:%S}  {p.wdir.path}"
                line += "" if p.accept_messages else "  (сообщения отложены)"
            self.out(line)
        self.out(f"Всего пользователей: {len(users)}")

    @command("change_password", "cpw", usage="change_password старый новый", group=G_USER, help="Сменить свой пароль.")
    def cmd_change_password(self, a):
        need(a, 2, "change_password старый новый")
        p = self.proc.principal
        if not self.system.users.check(p.person, p.project, a[0]):
            raise BadArgument("Старый пароль неверен")
        self.system.users.set_password(p.person, p.project, a[1])
        self.out("Пароль изменён.")

    # -------------------------------------------------------- сообщения
    @command("send_message", "sm", usage="send_message Person[.Project] текст", group=G_USER,
             help="Мгновенное сообщение пользователю (показывается при его следующей команде).")
    def cmd_send_message(self, a):
        need(a, 2, "send_message Person[.Project] текст")
        self.out(self.system.send_message(self.proc, a[0], " ".join(a[1:])))

    @command("accept_messages", "am", group=G_USER, help="Получать сообщения сразу.")
    def cmd_accept_messages(self, a):
        self.proc.accept_messages = True
        self.out("Сообщения принимаются.")

    @command("defer_messages", "dm", group=G_USER, help="Откладывать сообщения (смотреть через print_messages).")
    def cmd_defer_messages(self, a):
        self.proc.accept_messages = False
        self.out("Сообщения откладываются; прочитать — print_messages.")

    @command("print_messages", "pm", group=G_USER, help="Показать отложенные сообщения.")
    def cmd_print_messages(self, a):
        if not self._show_messages():
            self.out("Нет сообщений.")

    def _show_messages(self):
        msgs = self.system.take_messages(self.proc.principal)
        for sender, text, when in msgs:
            self.out(f"*** Сообщение от {sender} ({when:%H:%M:%S}): {text}")
        return bool(msgs)

    # ------------------------------------------------------------- почта
    @command("send_mail", "sdm", usage="send_mail Person[.Project] текст", group=G_USER,
             help="Отправить письмо в почтовый ящик пользователя.")
    def cmd_send_mail(self, a):
        need(a, 2, "send_mail Person[.Project] текст")
        person, project = self.system.send_mail(self.proc, a[0], " ".join(a[1:]))
        self.out(f"Письмо отправлено {person}.{project}")

    @command("read_mail", "rdm", group=G_USER, help="Прочитать почтовый ящик.")
    def cmd_read_mail(self, a):
        box = self.system.mail.get(self.system.key(self.proc.principal), [])
        if not box:
            self.out("Почтовый ящик пуст.")
        for i, m in enumerate(box, 1):
            mark = "" if m["read"] else " [новое]"
            self.out(f"  {i}. от {m['from']} {m['time']:%d.%m %H:%M}{mark}: {m['text']}")
            m["read"] = True

    @command("delete_mail", "dlm", usage="delete_mail номер|-all", group=G_USER, help="Удалить письмо из ящика.")
    def cmd_delete_mail(self, a):
        need(a, 1, "delete_mail номер|-all")
        box = self.system.mail.setdefault(self.system.key(self.proc.principal), [])
        if a[0] == "-all":
            n = len(box)
            box.clear()
            self.out(f"Удалено писем: {n}")
            return
        i = to_int(a[0], "номер письма", 1)
        if i > len(box):
            raise NotFound(f"Нет письма №{i}")
        box.pop(i - 1)
        self.out(f"Письмо №{i} удалено")

    @command("print_motd", "pmotd", group=G_USER, help="Показать сообщение дня (>sss>motd).")
    def cmd_print_motd(self, a):
        motd = self._resolve(">sss>motd")
        self.system.reference(self.proc, motd, "r")
        self.out(motd.content or "(пусто)")

    # --------------------------------------------------- администрирование
    @command("new_user", "nu", usage="new_user Person.Project пароль", group=G_ADMIN,
             help="Зарегистрировать пользователя и создать домашний каталог (только SysAdmin).")
    def cmd_new_user(self, a):
        need(a, 2, "new_user Person.Project пароль")
        parts = a[0].split(".")
        if len(parts) != 2:
            raise BadArgument("Нужен идентификатор вида Person.Project")
        self.system.new_user(self.proc, parts[0], parts[1], a[1])
        self.out(f"Пользователь {a[0]} зарегистрирован, домашний каталог >udd>{parts[1]}>{parts[0]}")

    @command("delete_user", "du", usage="delete_user Person.Project", group=G_ADMIN,
             help="Удалить регистрацию пользователя (каталог сохраняется; только SysAdmin).")
    def cmd_delete_user(self, a):
        need(a, 1, "delete_user Person.Project")
        parts = a[0].split(".")
        if len(parts) != 2:
            raise BadArgument("Нужен идентификатор вида Person.Project")
        self.system.delete_user(self.proc, parts[0], parts[1])
        self.out(f"Регистрация {a[0]} удалена")

    @command("list_users", "lu", group=G_ADMIN, help="Список зарегистрированных пользователей (только SysAdmin).")
    def cmd_list_users(self, a):
        self.system.require_admin(self.proc)
        for person, project in self.system.users.all():
            on = " (в системе)" if self.system.logged_in(person, project) else ""
            self.out(f"  {person}.{project}{on}")

    @command("bump", usage="bump pid", group=G_ADMIN,
             help="Принудительно завершить процесс (свой — любому, чужой — только SysAdmin).")
    def cmd_bump(self, a):
        need(a, 1, "bump pid")
        target = self.system.bump(self.proc, to_int(a[0], "номер процесса"))
        if target in self.stack:
            was_current = target is self.proc
            self.stack.remove(target)
            if was_current:
                self._announce_current()
