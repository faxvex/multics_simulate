"""Реестр пользователей: Person.Project + пароль (хранится как хеш с солью)."""
import hashlib
import os

from .errors import AlreadyExists, BadArgument, NotFound


class UserRegistry:
    def __init__(self):
        self._users = {}   # (person, project) -> (salt, hash)

    @staticmethod
    def _hash(salt, password):
        return hashlib.sha256(salt + password.encode("utf-8")).hexdigest()

    def register(self, person, project, password):
        if (person, project) in self._users:
            raise AlreadyExists(f"Пользователь {person}.{project} уже зарегистрирован")
        for part in (person, project):
            if not part.isidentifier():
                raise BadArgument(f"Недопустимое имя «{part}» (буквы, цифры, '_')")
        self._set(person, project, password)

    def _set(self, person, project, password):
        if len(password) < 3:
            raise BadArgument("Пароль должен быть не короче 3 символов")
        salt = os.urandom(8)
        self._users[(person, project)] = (salt, self._hash(salt, password))

    def set_password(self, person, project, password):
        self._require(person, project)
        self._set(person, project, password)

    def remove(self, person, project):
        self._require(person, project)
        del self._users[(person, project)]

    def _require(self, person, project):
        if (person, project) not in self._users:
            raise NotFound(f"Нет пользователя {person}.{project}")

    def exists(self, person, project):
        return (person, project) in self._users

    def check(self, person, project, password):
        rec = self._users.get((person, project))
        return rec is not None and rec[1] == self._hash(rec[0], password)

    def all(self):
        return sorted(self._users)

    def projects_of(self, person):
        return sorted(p for (who, p) in self._users if who == person)
