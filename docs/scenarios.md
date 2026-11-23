# Сценарии практических занятий

Запуск: `python -m multics_sim`. Пароли: `ivan` (Ivanov.Students), `petr` (Petrov.Students), `guest` (Guest.Visitors).
Команды ниже взяты из демонстрационного сценария (`multics_sim/demo.py`), их вывод можно сверить с `docs/demo_output.txt`.

## Сценарий 1. Защита: ACL и кольца (15–20 мин)

```
login Ivanov.Students ivan
create_dir work
change_wdir work
create notes Конспект
print >sss>kernel_data
effective_access >sss>kernel_data
set_ring_brackets notes 4 4 4
create -exec gate_test "echo [gate_test] я в кольце пользователя; ring; call hcs_; ring; echo [gate_test] снова в пользовательском кольце"
run gate_test
```

Ожидается: отказ **по кольцу** (а не по ACL) при чтении `>sss>kernel_data`; таблица `effective_access`
показывает ACL «да», кольцо «нет»; при `run gate_test` ядро сообщает о входе через шлюз 4 → 0 и о возврате.

Вопросы: Почему отказ произошёл по кольцу, а не по ACL? Что изменилось в состоянии процесса после вызова шлюза?
Почему `set_ring_brackets notes 2 2 2` отклонено?

Дополнение (ACL между пользователями): `login Petrov.Students petr`, `print >udd>Students>Ivanov>work>notes`
(отказ) → `switch 1` → `set_acl notes r Petrov.Students` → `switch 2` → снова `print ...` (успех) →
`set_acl ... rew Petrov.Students` (отказ: у Petrov нет права `m`/`w` на изменение ACL).

## Сценарий 2. Динамическое связывание (15 мин)

```
create -exec util "echo [util] библиотечная процедура util выполнена"
create -exec main "echo [main] начало; call util; call util; call sss_hello; echo [main] конец"
print_search_rules
where sss_hello
run main
print_linkage
list_ref_names
```

Ожидается: при первом `call util` — linkage fault, поиск по правилам, защёлкивание ссылки; при втором — прямой
вызов без fault. Вопросы: Где найден `sss_hello` и почему не в рабочем каталоге? Что изменится после `reset_links`?

## Сценарий 3. Виртуальная память и планировщик (20–25 мин)

```
create big
fill big 300
memstat
page_table big
print -q big
set_policy lru
print -q big
memstat
set_policy clock
reset_links
create -exec job1 "echo [job1] старт; compute 3; call util; echo [job1] готово"
start job1
switch 2
create -exec job2 "echo [job2] старт; compute 3; call sss_hello; echo [job2] готово"
start job2
ps
schedule
ps
```

(`switch 2` требует, чтобы второй пользователь уже был залогинен через `login Petrov.Students petr`.)
Задание: сравнить число page fault для clock, FIFO и LRU на одном наборе обращений; объяснить порядок вытеснения.

## Задания для самостоятельной работы

1. Выполнить `python -m multics_sim --demo` и по протоколу ядра описать, какие механизмы задействованы в разделах 3–7.
2. Изменить ACL и кольцевые скобки сегмента и **предсказать** результат доступа до выполнения команды.
3. Написать исполняемый сегмент на мини-языке (`echo`, `call`, `read`, `store`, `compute`, `ring`) и проследить linkage fault.
4. Сравнить три алгоритма замещения страниц на одном наборе обращений.
