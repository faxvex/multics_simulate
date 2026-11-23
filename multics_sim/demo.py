"""Демонстрационный сценарий: последовательно показывает все механизмы.
Строки, начинающиеся с '##', — заголовки разделов; остальные — команды оболочки.
Текст в квадратных скобках внутри кавычек не вычисляется (как в Multics)."""
from .shell import Shell
from .system import System

SCRIPT = [
    "## 1. Пользователи: login / who / ring",
    "who",
    "login Ivanov.Students неверный",
    "login Ivanov.Students ivan",
    "who", "ring", "print_wdir",

    "## 2. Иерархическая файловая система (разделитель '>')",
    "list >", "list >udd>Students", "list >sss",
    "create_dir work", "change_wdir work", "print_wdir",
    "create_dir >hack",
    "create notes Конспект по Multics: всё есть сегмент",
    "status notes", "list", "change_wdir <", "print_wdir", "change_wdir work",

    "## 3. ACL: проверка прав по Person.Project.tag",
    "list_acl notes",
    "login Petrov.Students petr",
    "print >udd>Students>Ivanov>work>notes",
    "switch 1",
    "set_acl notes r Petrov.Students",
    "list_acl notes",
    "switch 2",
    "print >udd>Students>Ivanov>work>notes",
    "set_acl >udd>Students>Ivanov>work>notes rew Petrov.Students",
    "switch 1",
    "delete_acl notes Petrov.Students",

    "## 4. Кольца защиты и кольцевые скобки",
    "print >sss>kernel_data",
    "effective_access >sss>kernel_data",
    "set_ring_brackets notes 2 2 2",
    "set_ring_brackets notes 4 4 4",
    'create -exec gate_test "echo [gate_test] я в кольце пользователя; ring; call hcs_; ring; '
    'echo [gate_test] снова в пользовательском кольце"',
    "run gate_test",
    "login Guest.Visitors guest",
    "create secret Личные заметки гостя",
    "ring 5",
    "print secret",
    "ring 4",
    "logout",

    "## 5. Динамическое связывание (linkage fault и защёлкивание ссылки)",
    "switch 1",
    'create -exec util "echo [util] библиотечная процедура util выполнена"',
    'create -exec main "echo [main] начало; call util; call util; call sss_hello; echo [main] конец"',
    "print_search_rules",
    "where sss_hello",
    "run main",
    "print_linkage", "list_ref_names",

    "## 6. Виртуальная память: страницы, кадры, алгоритмы замены",
    "create big",
    "fill big 300",
    "memstat",
    "page_table big",
    "print -q big",
    "touch big 290",
    "set_policy lru",
    "print -q notes",
    "print -q big",
    "memstat",
    "set_policy clock",

    "## 7. Процессы и планировщик round-robin",
    "reset_links",
    'create -exec job1 "echo [job1] старт; compute 3; call util; echo [job1] готово"',
    "start job1",
    "switch 2",
    'create -exec job2 "echo [job2] старт; compute 3; call sss_hello; echo [job2] готово"',
    "start job2",
    "ps",
    "schedule",
    "ps",

    "## 8. Файлы: имена, ссылки, копии, safety switch, дерево",
    "switch 1",
    "add_name notes konspekt",
    "copy konspekt notes.bak",
    "create_dir archive",
    "move notes.bak archive",
    "link >sss>sss_hello hello",
    "safety_sw_on notes",
    "delete notes",
    "list",
    "tree",

    "## 9. Активные функции и сценарии exec_com",
    "echo Пользователь [user], каталог [wd], 2+3*4 = [plus 2 [times 3 4]]",
    "echo Сегменты: [segments **]",
    'if [exists segment notes] -then "echo notes на месте" -else "echo notes нет"',
    "value_set course Операционные системы",
    "echo Курс: [value_get course]",
    'create backup.ec "&- сценарий: копия сегмента с суффиксом .old; '
    '&if [exists segment &1] &then &goto ok; &print Нет сегмента &1; &quit; '
    '&label ok; copy &1 &1.old; &print Готово: [entry &1].old"',
    "exec_com backup util",
    "exec_com backup nothing_here",
    'do "status &1" util.old',
    "abbrev add ll list -seg",
    "ll",

    "## 10. Связь, печать и администрирование",
    "send_message Petrov Загляни в >udd>Students>Ivanov>work",
    "send_mail Petrov.Students Сдаём реферат в пятницу",
    "dprint util",
    "switch 2",
    "read_mail",
    "schedule",
    "login Admin.SysAdmin admin",
    "new_user Sidorov.Students sid",
    "list_users",
    "logout",

    "## 11. Завершение: delete, logout",
    "switch 1",
    "safety_sw_off notes",
    "delete big",
    "memstat",
    "logout",
    "who",
    "logout",
    "who",
]


def run_demo(out=print):
    shell = Shell(System(out), out)
    for line in SCRIPT:
        if line.startswith("##"):
            out("")
            out("=" * 78)
            out(line[3:])
            out("=" * 78)
        else:
            out(f"{shell.prompt()}{line}")
            shell.execute(line)
    out("")
    out("Демонстрация завершена.")
