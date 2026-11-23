"""Сегмент — единая сущность файловой системы (файл, каталог или ссылка)."""
from datetime import datetime

from .acl import Acl
from .rings import RingBrackets


class Segment:
    """Узел иерархии. Каталог — это сегмент с is_dir=True и словарём children.

    Номер сегмента segno выдаётся один раз при создании. (В настоящем Multics
    номер сегмента — свойство процесса, см. KST; здесь для наглядности у каждого
    файла есть собственный номер, а KST хранит, какие сегменты процесс «знает».)

    Как в Multics, у записи каталога может быть несколько имён (add_name):
    names[0] — основное имя, все имена указывают на один и тот же сегмент.
    Ссылка (link) — запись, хранящая путь к другому объекту (link_target).
    """

    def __init__(self, segno, name, parent, is_dir, brackets: RingBrackets):
        self.segno = segno
        self.names = [name]
        self.parent = parent
        self.is_dir = is_dir
        self.brackets = brackets
        self.acl = Acl()
        self.iacl_seg = Acl()      # начальный ACL для новых сегментов (только каталоги)
        self.content = ""          # «содержимое» — текст / мини-скрипт
        self.executable = False    # процедурный сегмент (можно исполнять)
        self.gate = False          # шлюз: допускает вызов из внешних колец
        self.safety = False        # safety switch: запрет удаления
        self.link_target = None    # для ссылок — путь, на который она указывает
        self.quota = None          # для каталогов: предел страниц на всё поддерево (None — нет)
        self.owner = ""
        self.created = self.modified = datetime.now()
        self.children = {}         # только для каталогов: имя -> Segment

    @property
    def name(self):
        return self.names[0]

    @name.setter
    def name(self, value):
        self.names[0] = value

    @property
    def is_link(self):
        return self.link_target is not None

    @property
    def path(self):
        if self.parent is None:
            return ">"
        parent = self.parent.path
        return (">" if parent == ">" else parent + ">") + self.name

    @property
    def size(self):
        return len(self.content)

    @property
    def kind(self):
        if self.is_link:
            return "ссылка"
        if self.is_dir:
            return "каталог"
        if self.executable:
            return "шлюз (gate)" if self.gate else "исполняемый"
        return "данные"
