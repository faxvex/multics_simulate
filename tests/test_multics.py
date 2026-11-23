"""Автотесты эмулятора: по одному-два теста на каждый механизм.
Запуск из папки проекта:  python -m unittest discover -s tests -t ."""
import unittest

from multics_sim.shell import Shell
from multics_sim.system import System


class Base(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.sh = Shell(System(self.log.append), self.log.append)

    def run_cmd(self, line):
        """Выполнить команду и вернуть её вывод одной строкой."""
        start = len(self.log)
        self.sh.execute(line)
        return "\n".join(self.log[start:])

    def login(self, who="Ivanov.Students", pw="ivan"):
        self.run_cmd(f"login {who} {pw}")


class TestUsers(Base):
    def test_wrong_password(self):
        self.assertIn("Неверное имя", self.run_cmd("login Ivanov.Students xxx"))
        self.assertIsNone(self.sh.proc)

    def test_login_who_logout(self):
        self.login()
        self.assertEqual(self.sh.proc.ring, 4)
        self.assertEqual(self.sh.proc.wdir.path, ">udd>Students>Ivanov")
        self.assertIn("Ivanov.Students.a", self.run_cmd("who"))
        self.assertIn("уже работает", self.run_cmd("login Ivanov.Students ivan"))
        self.run_cmd("logout")
        self.assertIn("никого нет", self.run_cmd("who"))


class TestFileSystem(Base):
    def test_paths_and_create(self):
        self.login()
        self.run_cmd("create_dir work")
        self.run_cmd("cwd work")
        self.assertEqual(self.run_cmd("pwd"), ">udd>Students>Ivanov>work")
        self.run_cmd("create notes текст")
        self.assertIn("notes", self.run_cmd("ls"))
        self.run_cmd("cwd <")
        self.assertEqual(self.run_cmd("pr work>notes"), "текст")

    def test_root_is_protected(self):
        self.login()
        self.assertIn("Отказ по ACL", self.run_cmd("create_dir >hack"))

    def test_cannot_delete_working_dir(self):
        self.login()
        self.run_cmd("create_dir work")
        self.run_cmd("cwd work")
        self.assertIn("рабочий каталог процесса", self.run_cmd("delete_dir >udd>Students>Ivanov>work"))


class TestAcl(Base):
    def test_grant_and_revoke(self):
        self.login()
        self.run_cmd("create notes секрет")
        self.login("Petrov.Students", "petr")
        path = ">udd>Students>Ivanov>notes"
        self.assertIn("Отказ по ACL", self.run_cmd(f"pr {path}"))
        self.run_cmd("switch 1")
        self.run_cmd("sa notes r Petrov.Students")
        self.run_cmd("switch 2")
        self.assertTrue(self.run_cmd(f"pr {path}").endswith("\nсекрет"))
        # писать Петрову по-прежнему нельзя, и менять ACL чужого файла тоже
        self.assertIn("Отказ по ACL", self.run_cmd(f"write {path} x"))
        self.assertIn("нет режима 'm'", self.run_cmd(f"sa {path} rew Petrov.Students"))

    def test_most_specific_entry_wins(self):
        self.login()
        self.run_cmd("create f данные")
        self.run_cmd("sa f r *.Students.*")
        self.run_cmd("sa f null Petrov.Students.*")
        self.login("Petrov.Students", "petr")
        self.assertIn("Отказ по ACL", self.run_cmd("pr >udd>Students>Ivanov>f"))

    def test_bad_modes(self):
        self.login()
        self.run_cmd("create f")
        self.assertIn("Недопустимые режимы", self.run_cmd("sa f sma"))


class TestRings(Base):
    def test_ring_blocks_despite_acl(self):
        self.login()
        out = self.run_cmd("pr >sss>kernel_data")   # ACL даёт r всем, но скобки 0,0,0
        self.assertIn("Отказ по кольцу", out)

    def test_list_shows_acl_and_effective_access(self):
        """В кольце 4: kernel_data — ACL r, но итог null; hcs_ — ACL re, итог e (через шлюз)."""
        self.login()
        rows = {line.split()[6]: line.split() for line in self.run_cmd("ls >sss").splitlines()
                if line.startswith("  data") or line.startswith("  gate") or line.startswith("  exec")}
        self.assertEqual(rows["kernel_data"][1:3], ["r", "null"])
        self.assertEqual(rows["hcs_"][1:3], ["re", "e"])
        self.assertEqual(rows["sss_hello"][1:3], ["re", "re"])

    def test_brackets_cannot_go_below_own_ring(self):
        self.login()
        self.run_cmd("create f")
        self.assertIn("меньше вашего кольца", self.run_cmd("srb f 2 2 2"))
        self.assertIn("недопустимы", self.run_cmd("srb f 5 4 6"))

    def test_outer_ring_and_no_way_back(self):
        self.login()
        self.run_cmd("create f x")
        self.run_cmd("ring 5")
        self.assertIn("Отказ по кольцу", self.run_cmd("pr f"))
        self.assertIn("нельзя", self.run_cmd("ring 4"))

    def test_gate_enters_ring0_and_returns(self):
        self.login()
        self.run_cmd("create -exec t ring; call hcs_; ring")
        out = self.run_cmd("run t")
        self.assertIn("вход через шлюз: кольцо 4 -> 0", out)
        self.assertIn("текущее кольцо = 0", out)
        self.assertNotIn("Ошибка", out)
        self.assertEqual(self.sh.proc.ring, 4)

    def test_non_gate_not_callable_from_outer_ring(self):
        self.login()
        self.run_cmd("create -exec x echo hi")
        self.run_cmd("srb x 4 4 6")          # кольцо 5 попадает в r2 < 5 <= r3
        self.run_cmd("ring 5")
        self.assertIn("не является шлюзом", self.run_cmd("run x"))
        self.run_cmd("ring 7")
        self.assertIn("недоступен из кольца 7", self.run_cmd("run x"))

    def test_suspended_gate_task_does_not_leak_ring0(self):
        """Задача стоит внутри шлюза (кольцо 0) — оболочка процесса должна остаться в кольце 4."""
        self.login()
        self.run_cmd("create -exec t call hcs_; compute 5")
        self.run_cmd("start t")
        task = self.sh.proc.tasks[0]
        for _ in range(20):
            self.sh.system.scheduler.run(1)
            if task.ring == 0:
                break
        self.assertEqual(task.ring, 0, "задача так и не вошла в шлюз")
        self.assertEqual(self.sh.proc.ring, 4)
        self.assertIn("Отказ по кольцу", self.run_cmd("pr >sss>kernel_data"))
        self.sh.system.scheduler.run()
        self.assertEqual(self.sh.proc.ring, 4)


class TestLinking(Base):
    def test_fault_once_then_snapped(self):
        self.login()
        self.run_cmd("create -exec util echo u")
        self.run_cmd("create -exec main call util; call util")
        out = self.run_cmd("run main")
        self.assertEqual(out.count("linkage fault:"), 1)
        self.assertIn("уже защёлкнута", out)
        self.assertNotIn("linkage fault:", self.run_cmd("run main"))
        self.assertIn("защёлкнута -> >udd>Students>Ivanov>util", self.run_cmd("links"))

    def test_search_rules_order(self):
        self.login()
        self.run_cmd("create -exec sss_hello echo local")   # перекрывает >sss>sss_hello
        self.run_cmd("create -exec m call sss_hello")
        self.assertIn("] local", self.run_cmd("run m"))

    def test_missing_target(self):
        self.login()
        self.run_cmd("create -exec m call nothing")
        self.assertIn("не найден по правилам поиска", self.run_cmd("run m"))


class TestMemory(Base):
    def test_clock_replacement(self):
        self.login()
        self.run_cmd("create big")
        self.run_cmd("fill big 300")                         # 5 страниц при 4 кадрах
        mem = self.sh.system.memory
        self.assertGreaterEqual(mem.faults, 5)
        self.assertGreaterEqual(mem.evictions, 1)
        self.assertLessEqual(len(mem.page_table), len(mem.frames))
        # таблица страниц и кадры согласованы
        for (segno, page), idx in mem.page_table.items():
            self.assertEqual((mem.frames[idx].segno, mem.frames[idx].page), (segno, page))
        before = mem.writebacks
        self.run_cmd("pr -q big")
        self.assertGreater(mem.writebacks, before)            # вытеснялись изменённые страницы

    def test_hit_does_not_fault(self):
        self.login()
        self.run_cmd("create small x")
        mem = self.sh.system.memory
        faults = mem.faults
        self.run_cmd("pr small")
        self.assertEqual(mem.faults, faults)

    def test_delete_frees_frames(self):
        self.login()
        self.run_cmd("create big")
        self.run_cmd("fill big 200")
        segno = self.sh.system.fs.resolve("big", self.sh.proc.wdir).segno
        self.run_cmd("dl big")
        self.assertEqual(self.sh.system.memory.resident_pages(segno), [])


class TestScheduler(Base):
    def test_round_robin_interleaves(self):
        self.login()
        self.run_cmd("create -exec a echo A1; echo A2; echo A3")
        self.run_cmd("start a")
        self.login("Petrov.Students", "petr")
        self.run_cmd("create -exec b echo B1; echo B2; echo B3")
        self.run_cmd("start b")
        out = self.run_cmd("schedule")
        order = [line.split()[-1] for line in out.splitlines() if line.startswith("[pid")]
        # квант = 2 инструкции: A1 A2 | B1 B2 | A3 | B3
        self.assertEqual(order, ["A1", "A2", "B1", "B2", "A3", "B3"])
        self.assertEqual(self.sh.system.scheduler.clock, 6)    # 6 инструкций = 6 тактов

    def test_bad_instruction_does_not_crash_shell(self):
        self.login()
        self.run_cmd("create -exec bad read")
        self.assertIn("нужен аргумент", self.run_cmd("run bad"))
        self.run_cmd("create -exec bad2 compute xyz")
        self.assertIn("Нужно целое число (число тактов)", self.run_cmd("run bad2"))
        self.assertEqual(self.sh.proc.ring, 4)
        self.assertIn("ожидает", self.run_cmd("ps"))

    def test_start_rejects_data_segment(self):
        self.login()
        self.run_cmd("create d x")
        self.assertIn("не является исполняемым", self.run_cmd("start d"))
        self.assertFalse(self.sh.proc.tasks)

    def test_logout_drops_tasks(self):
        self.login()
        self.run_cmd("create -exec a compute 10")
        self.run_cmd("start a")
        self.assertIn("Снято незавершённых задач", self.run_cmd("logout"))
        self.assertEqual(len(self.sh.system.scheduler.queue), 0)


class TestShell(Base):
    def test_russian_number_errors(self):
        self.login()
        self.assertIn("Нужно целое число (номер кольца)", self.run_cmd("ring abc"))
        self.assertIn("Команды (в скобках", self.run_cmd("﻿help"))   # BOM не мешает разбору

    def test_aliases_exist(self):
        for alias in ("ls", "cwd", "pwd", "dl", "pr", "sa", "la", "srb", "st", "ms"):
            self.assertIn(alias, self.sh.commands)

    def test_demo_runs_without_internal_errors(self):
        from multics_sim.demo import run_demo
        log = []
        run_demo(log.append)
        text = "\n".join(log)
        self.assertIn("Демонстрация завершена", text)
        for bad in ("Не найдено", "Traceback", "Внутренняя ошибка", "Нет активной функции",
                    "Неизвестная команда", "Использование:", "Незакрытая", "Нет закрывающей"):
            self.assertNotIn(bad, text, f"в демо встретилось «{bad}»")


class TestFileOps(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.run_cmd("create a текст А")

    def test_rename_and_names(self):
        self.run_cmd("rename a b")
        self.assertIn("текст А", self.run_cmd("pr b"))
        self.run_cmd("add_name b c")
        self.assertIn("текст А", self.run_cmd("pr c"))          # одно содержимое — два имени
        self.run_cmd("delete_name c")
        self.assertIn("единственное имя", self.run_cmd("delete_name b"))

    def test_copy_move_keeps_segno(self):
        self.run_cmd("create_dir d")
        self.run_cmd("copy a d")
        self.assertIn("текст А", self.run_cmd("pr d>a"))
        segno = self.sh.system.fs.resolve("a", self.sh.proc.wdir).segno
        self.run_cmd("move a d>a2")
        self.assertEqual(self.sh.system.fs.resolve("d>a2", self.sh.proc.wdir).segno, segno)
        self.assertIn("внутрь самого себя", self.run_cmd("move d d>sub"))

    def test_links(self):
        self.run_cmd("link >sss>sss_hello hi")
        self.assertIn("системная библиотека", self.run_cmd("run hi"))
        self.assertIn("ссылка; используйте unlink", self.run_cmd("delete hi"))
        self.run_cmd("unlink hi")
        self.run_cmd("link nowhere broken")
        self.assertIn("несуществующий", self.run_cmd("pr broken"))
        self.run_cmd("link loop2 loop1")
        self.run_cmd("link loop1 loop2")
        self.assertIn("цепочка ссылок", self.run_cmd("pr loop1"))

    def test_safety_switch_and_force(self):
        self.run_cmd("safety_sw_on a")
        self.assertIn("safety switch", self.run_cmd("dl a"))
        self.run_cmd("create_dir d")
        self.run_cmd("create d>x 1")
        self.assertIn("не пуст", self.run_cmd("dd d"))
        self.run_cmd("safety_sw_on d>x")
        self.assertIn("safety switch", self.run_cmd("dd d -force"))
        self.assertTrue(self.sh.system.fs.resolve("d>x", self.sh.proc.wdir))    # ничего не удалено частично
        self.run_cmd("safety_sw_off d>x")
        self.assertIn("Удалён каталог", self.run_cmd("dd d -force"))

    def test_starnames(self):
        for n in ("r1.txt", "r2.txt", "keep.dat"):
            self.run_cmd(f"create {n} x")
        self.assertEqual(self.run_cmd("echo [segments *.txt]"), "r1.txt r2.txt")
        self.run_cmd("delete *.txt")
        self.assertEqual(self.run_cmd("echo [segments *]"), "a")             # * — ровно один компонент имени
        self.assertEqual(self.run_cmd("echo [segments **]"), "a keep.dat")   # ** — любые компоненты
        self.assertEqual(self.run_cmd("echo [segments]"), "a keep.dat")

    def test_edit_lines(self):
        self.run_cmd("create t b;a;c")
        self.run_cmd("sort_seg t")
        self.assertEqual(self.run_cmd("pr t"), "a\nb\nc")
        self.run_cmd("append t d")
        self.assertIn("   4  d", self.run_cmd("pr t 4"))
        self.run_cmd("truncate t 1")
        self.assertEqual(self.run_cmd("pr t"), "a")

    def test_compare_ascii(self):
        self.run_cmd("create b текст А")
        self.assertIn("идентичны", self.run_cmd("cpa a b"))
        self.run_cmd("write b другой")
        self.assertIn("+другой", self.run_cmd("cpa a b"))

    def test_edm(self):
        lines = iter(["a", "первая", "вторая", ".", "r 1 ПЕРВАЯ", "s/вторая/2/", "w", "q"])
        self.sh.input_fn = lambda prompt: next(lines)
        self.run_cmd("edm doc")
        self.assertEqual(self.run_cmd("pr doc"), "ПЕРВАЯ\n2")

    def test_tree_and_walk(self):
        self.run_cmd("create_dir d")
        self.run_cmd("create d>x 1")
        self.assertIn("└── x  (1)", self.run_cmd("tree"))
        out = self.run_cmd(f"walk_subtree {self.sh.proc.wdir.path} pwd")
        self.assertIn(">udd>Students>Ivanov>d", out)
        self.assertEqual(self.sh.proc.wdir.path, ">udd>Students>Ivanov")   # wdir восстановлен


class TestAccessExtra(Base):
    def test_iacl_applies_to_new_segments(self):
        self.login()
        self.run_cmd("set_iacl_seg >udd>Students>Ivanov r *.Students.*")
        self.run_cmd("create shared x")
        self.login("Petrov.Students", "petr")
        self.assertEqual(self.run_cmd("pr >udd>Students>Ivanov>shared").split("\n")[-1], "x")

    def test_effective_access_explains(self):
        self.login()
        out = self.run_cmd("ea >sss>kernel_data")
        self.assertIn("r      да   нет", out)            # ACL разрешает, кольцо — нет

    def test_dir_brackets_and_copy_acl(self):
        self.login()
        self.run_cmd("create_dir d")
        self.assertIn("используйте set_dir_ring_brackets", self.run_cmd("srb d 4 4 4"))
        self.assertIn("4,5,5", self.run_cmd("sdrb d 4 5"))
        self.run_cmd("create s1 x")
        self.run_cmd("create s2 y")
        self.run_cmd("sa s1 r Petrov.Students")
        self.run_cmd("copy_acl s1 s2")
        self.assertIn("Petrov.Students.*", self.run_cmd("la s2"))


class TestCommunication(Base):
    def test_messages_and_defer(self):
        self.login()
        self.login("Petrov.Students", "petr")
        self.run_cmd("defer_messages")
        self.run_cmd("switch 1")
        self.assertIn("отложил", self.run_cmd("sm Petrov привет"))
        self.assertNotIn("привет", self.run_cmd("switch 2"))
        self.assertIn("привет", self.run_cmd("print_messages"))

    def test_message_to_offline_user_shown_at_login(self):
        self.login()
        self.run_cmd("sm Guest привет гостю")
        self.assertIn("привет гостю", self.run_cmd("login Guest.Visitors guest"))

    def test_mail(self):
        self.login()
        self.run_cmd("send_mail Petrov.Students письмо")
        self.assertIn("новых писем: 1", self.run_cmd("login Petrov.Students petr"))
        self.assertIn("[новое]: письмо", self.run_cmd("read_mail"))
        self.assertNotIn("[новое]", self.run_cmd("read_mail"))
        self.run_cmd("delete_mail 1")
        self.assertIn("пуст", self.run_cmd("rdm"))

    def test_dprint_daemon(self):
        self.login()
        self.run_cmd("create f строка1;строка2")
        self.run_cmd("dprint f")
        self.assertIn("№1", self.run_cmd("ldr"))
        out = self.run_cmd("schedule")
        self.assertIn("| строка2", out)
        self.assertIn("напечатан, строк: 2", out)

    def test_admin_only(self):
        self.login()
        self.assertIn("только администраторам", self.run_cmd("new_user X.Y pass"))
        self.assertIn("только администратор", self.run_cmd("login Petrov.Students petr") + self.run_cmd("bump 1"))
        self.login("Admin.SysAdmin", "admin")
        self.run_cmd("new_user Sidorov.Students sid")
        self.run_cmd("bump 2")                               # администратор завершает Петрова
        self.assertNotIn("Petrov", self.run_cmd("who"))
        self.run_cmd("logout")
        self.run_cmd("logout")
        self.run_cmd("login Sidorov.Students sid")
        self.assertEqual(self.sh.proc.wdir.path, ">udd>Students>Sidorov")

    def test_change_password_and_new_proc(self):
        self.login()
        self.run_cmd("cpw ivan newpass")
        self.run_cmd("create -exec u echo u")
        self.run_cmd("create -exec m call u")
        self.run_cmd("run m")
        old_pid = self.sh.proc.pid
        self.run_cmd("new_proc")
        self.assertNotEqual(self.sh.proc.pid, old_pid)
        self.assertEqual(self.sh.proc.linkage, {})            # новый процесс — чистые связи
        self.run_cmd("logout")
        self.assertIn("Неверное", self.run_cmd("login Ivanov.Students ivan"))
        self.run_cmd("login Ivanov.Students newpass")
        self.assertIsNotNone(self.sh.proc)

    def test_process_dir_lifecycle(self):
        self.login()
        pd = self.run_cmd("echo [pd]")
        self.assertTrue(pd.startswith(">pdd>!pid_"))
        self.run_cmd(f"create {pd}>tmp x")
        self.run_cmd("logout")
        self.login("Petrov.Students", "petr")
        self.assertIn("Не найдено", self.run_cmd(f"ls {pd}"))


class TestProcessExtra(Base):
    def test_search_rules_and_where(self):
        self.login()
        self.run_cmd("create_dir lib")
        self.run_cmd("create -exec lib>tool echo из lib")
        self.run_cmd("create -exec m call tool")
        self.assertIn("не найден по правилам", self.run_cmd("run m"))
        self.run_cmd("asr >udd>Students>Ivanov>lib")
        self.assertIn("из lib", self.run_cmd("run m"))
        self.assertIn("<- будет использован", self.run_cmd("where tool"))
        self.run_cmd("ssr default")
        self.assertEqual(len(self.sh.proc.search_rules), 2)

    def test_initiate_terminate(self):
        self.login()
        self.run_cmd("create -exec u echo u")
        self.run_cmd("create -exec m call u")
        self.run_cmd("run m")
        self.assertIn("разорвано ссылок: 1", self.run_cmd("terminate u"))
        self.assertIn("linkage fault:", self.run_cmd("run m"))
        self.assertIn("Отказ", self.run_cmd("in >sss>kernel_data"))      # ни один режим не проходит
        self.assertIn("каталог", self.run_cmd("in >sss"))

    def test_quantum(self):
        self.login()
        self.run_cmd("set_quantum 3")
        self.run_cmd("create -exec a echo A1;echo A2;echo A3")
        self.run_cmd("start a")
        self.login("Petrov.Students", "petr")
        self.run_cmd("create -exec b echo B1;echo B2;echo B3")
        self.run_cmd("start b")
        order = [l.split()[-1] for l in self.run_cmd("schedule").splitlines() if l.startswith("[pid")]
        self.assertEqual(order, ["A1", "A2", "A3", "B1", "B2", "B3"])


class TestMemoryPolicies(Base):
    def _setup(self, policy):
        self.login()
        for n in "abcd":
            self.run_cmd(f"create {n} {n}")
        self.run_cmd("set_frames 3")
        self.run_cmd(f"set_policy {policy}")
        for n in "abca":                 # a, b, c загружены; к a обратились ещё раз
            self.run_cmd(f"touch {n} 0")
        self.run_cmd("touch d 0")        # нужна жертва
        mem, fs = self.sh.system.memory, self.sh.system.fs
        return {n for n in "abcd" if mem.resident_pages(fs.resolve(n, self.sh.proc.wdir).segno)}

    def test_fifo_evicts_oldest(self):
        self.assertEqual(self._setup("fifo"), {"b", "c", "d"})

    def test_lru_evicts_least_recent(self):
        self.assertEqual(self._setup("lru"), {"a", "c", "d"})

    def test_clock_gives_second_chance(self):
        # все used=1 -> часы сбрасывают биты по кругу и вытесняют первый кадр (a)
        self.assertEqual(self._setup("clock"), {"b", "c", "d"})

    def test_touch_hit_and_page_table(self):
        self.login()
        self.run_cmd("create x data")
        self.assertIn("Попадание", self.run_cmd("touch x 0"))
        self.assertIn("стр. 0: кадр", self.run_cmd("page_table x"))
        self.assertIn("вне сегмента", self.run_cmd("touch x 999"))


class TestActiveFunctions(Base):
    def af(self, expr):
        return self.run_cmd(f"echo {expr}")

    def test_arithmetic_and_nesting(self):
        self.assertEqual(self.af("[plus 2 [times 3 4]]"), "14")
        self.assertEqual(self.af("[divide 7 2]"), "3.5")
        self.assertEqual(self.af("[quotient 7 2] [mod 7 2]"), "3 1")
        self.assertEqual(self.af("[calc (2+3)*4^2]"), "80")
        self.assertIn("Деление на ноль", self.af("[divide 1 0]"))
        self.assertIn("Недопустимое выражение", self.af("[calc __import__(1)]"))

    def test_strings(self):
        self.assertEqual(self.af("[substr Multics 2 3]"), "ult")
        self.assertEqual(self.af("[index Multics tic]"), "4")
        self.assertEqual(self.af("[upper_case abc] [reverse abc] [length abcd]"), "ABC cba 4")
        self.assertEqual(self.af("[before a.b.c .] [after a.b.c .]"), "a b.c")
        self.assertEqual(self.af('[format_line "^a = ^d" x 42]'), "x = 42")

    def test_logic_and_if(self):
        self.assertEqual(self.af("[and true [not false]]"), "true")
        self.assertEqual(self.af("[if [nless 2 10] -then да -else нет]"), "да")
        self.assertEqual(self.af("[if [less 2 10] -then да -else нет]"), "нет")   # строки: "2" > "1"
        self.assertIn("true или false", self.af("[not maybe]"))

    def test_quoting(self):
        self.assertEqual(self.run_cmd('echo "[plus 1 1]"'), "[plus 1 1]")            # в кавычках не вычисляется
        self.assertEqual(self.run_cmd('echo "say ""hi"""'), 'say "hi"')
        self.login()
        self.run_cmd('create "x y" 1')
        self.assertIn("Недопустимое имя", self.run_cmd('create "x y" 1'))

    def test_paths_and_user(self):
        self.login()
        self.assertEqual(self.af("[wd] [hd]"), ">udd>Students>Ivanov >udd>Students>Ivanov")
        self.assertEqual(self.af("[user person] [user project]"), "Ivanov Students")
        self.assertEqual(self.af("[strip report.txt txt] [suffix report.txt] [entry >a>b>c]"), "report txt c")
        self.assertEqual(self.af("[exists directory >sss] [exists segment >sss]"), "true false")

    def test_af_as_command_and_unknown(self):
        self.assertEqual(self.run_cmd("plus 1 2"), "3")
        self.assertIn("Нет активной функции", self.run_cmd("echo [nope]"))
        self.assertIn("Сначала выполните вход", self.run_cmd("echo [wd]"))

    def test_values_persist_between_sessions(self):
        self.login()
        self.run_cmd("value_set x 42")
        self.run_cmd("logout")
        self.login()
        self.assertEqual(self.af("[value_get x]"), "42")


class TestScripts(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_exec_com_args_if_goto(self):
        self.run_cmd('create loop.ec "value_set i 0; &label top; '
                     '&if [nless [value_get i] &1] &then &goto body; &quit; '
                     '&label body; &print шаг [value_get i]; value_set i [plus [value_get i] 1]; &goto top"')
        out = self.run_cmd("ec loop 3")
        self.assertEqual([l for l in out.splitlines() if l.startswith("шаг")], ["шаг 0", "шаг 1", "шаг 2"])

    def test_exec_com_infinite_loop_is_stopped(self):
        self.run_cmd('create inf.ec "&label a; &goto a"')
        self.assertIn("превышено", self.run_cmd("ec inf"))

    def test_exec_com_args_count(self):
        self.run_cmd('create n.ec "&print &n аргументов: &f1"')
        self.assertEqual(self.run_cmd("ec n a b c"), "3 аргументов: a b c")

    def test_do_and_abbrev_and_history(self):
        self.run_cmd("create f x")
        self.run_cmd('do "copy &1 &1.bak" f')
        self.assertIn("x", self.run_cmd("pr f.bak"))
        self.run_cmd("abbrev add p print")
        self.assertEqual(self.run_cmd("p f").split("\n")[-1], "x")
        self.assertIn("abbrev add p print", self.run_cmd("history"))
        self.assertEqual(self.run_cmd("redo").split("\n")[0], "redo> history")
        self.assertEqual(self.run_cmd("redo 2").split("\n")[0], "redo> create f x")

    def test_history_hides_passwords(self):
        self.run_cmd("cpw ivan secret2")
        self.login("Petrov.Students", "petr")
        hist = self.run_cmd("history")
        for secret in ("ivan", "secret2", "petr"):
            self.assertNotIn(f" {secret}", hist.replace("Ivanov", "").replace("Petrov", ""))
        self.assertIn("login Ivanov.Students ****", hist)
        self.assertIn("пароль", self.run_cmd("redo 1"))

    def test_walk_subtree_with_logout_does_not_touch_other_process(self):
        self.login("Petrov.Students", "petr")
        petrov_wdir = self.sh.proc.wdir
        self.run_cmd("switch 1")
        self.run_cmd("create_dir d1")
        self.run_cmd("create_dir d1>d2")
        out = self.run_cmd("walk_subtree d1 logout")
        self.assertIn("обход прерван", out)
        self.assertIs(self.sh.proc.wdir, petrov_wdir)       # wdir Петрова не тронут

    def test_ready_message(self):
        self.run_cmd("ready_on")
        out = self.run_cmd("create -exec p compute 5")
        self.assertRegex(out.splitlines()[-1], r"^r \d\d:\d\d 0 \d+$")
        self.assertRegex(self.run_cmd("run p").splitlines()[-1], r"^r \d\d:\d\d 5 \d+$")
        self.run_cmd("ready_off")
        self.assertNotIn("\nr ", "\n" + self.run_cmd("pwd"))

    def test_quota(self):
        self.run_cmd("create_dir q")
        self.run_cmd("set_quota q 2")                       # 2 страницы по 64 символа
        self.run_cmd("create q>a")
        self.assertIn("2 стр.", self.run_cmd("fill q>a 100"))
        self.assertIn("Превышена квота", self.run_cmd("fill q>a 200"))
        self.assertIn("Превышена квота", self.run_cmd("create q>b x"))
        self.run_cmd("create big")
        self.run_cmd("fill big 300")
        self.assertIn("Превышена квота", self.run_cmd("move big q"))
        self.run_cmd("truncate q>a")
        self.assertIn("Перемещено", self.run_cmd("move q>a q>a2"))  # перенос внутри квоты — без проверки
        self.assertIn("занято 0", self.run_cmd("get_quota q"))
        self.login("Petrov.Students", "petr")
        self.assertIn("нет режима 'm'", self.run_cmd("set_quota >udd>Students>Ivanov>q -none"))

    def test_page_table_requires_access(self):
        self.assertIn("Отказ по кольцу", self.run_cmd("page_table >sss>kernel_data"))

    def test_help(self):
        self.assertIn("== Файловая система ==", self.run_cmd("help"))
        self.assertIn("== Арифметика ==", self.run_cmd("help -af"))
        self.assertIn("set_acl", self.run_cmd("help *acl*"))
        self.assertIn("алиасы: sa", self.run_cmd("help set_acl"))


class TestPasswordPrompt(unittest.TestCase):
    """Пароль, не указанный в команде, запрашивается обычным вводом (не getpass)."""

    def test_login_asks_password(self):
        log, answers = [], iter(["ivan"])
        sh = Shell(System(log.append), log.append, input_fn=lambda prompt: next(answers))
        sh.execute("login Ivanov.Students")
        self.assertIsNotNone(sh.proc)
        self.assertNotIn("ivan", sh.history[-1].split())

    def test_login_no_input(self):
        log = []
        sh = Shell(System(log.append), log.append, input_fn=lambda prompt: (_ for _ in ()).throw(EOFError))
        sh.execute("login Ivanov.Students")
        self.assertIsNone(sh.proc)
        self.assertIn("Пароль не введён", "\n".join(log))


class TestLoginHint(unittest.TestCase):
    def test_wrong_case_hint(self):
        log = []
        sh = Shell(System(log.append), log.append)
        sh.execute("login ivanov.Students ivan")
        self.assertIsNone(sh.proc)
        self.assertIn("регистр букв важен: Ivanov.Students", "\n".join(log))
        log.clear()
        sh.execute("login Nobody.Students x")
        self.assertNotIn("регистр", "\n".join(log))


if __name__ == "__main__":
    unittest.main()
