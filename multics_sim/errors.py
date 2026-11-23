"""Исключения эмулятора. Все «ожидаемые» ошибки ОС наследуют MulticsError;
командный интерпретатор ловит их и печатает сообщение пользователю."""


class MulticsError(Exception):
    """Базовая ошибка ОС."""


class AccessViolation(MulticsError):
    """Нарушение защиты: не хватает прав в ACL либо запрещает кольцо."""


class NotFound(MulticsError):
    """Сегмент или каталог не найден."""


class AlreadyExists(MulticsError):
    """Имя уже занято в каталоге."""


class BadArgument(MulticsError):
    """Неверный аргумент команды."""


def to_int(text, what="число", minimum=0):
    """Разбор целого числа с русским сообщением об ошибке."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BadArgument(f"Нужно целое число ({what}), получено «{text}»") from None
    if value < minimum:
        raise BadArgument(f"{what.capitalize()} должно быть не меньше {minimum}, получено {value}")
    return value
