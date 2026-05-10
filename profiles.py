"""
Система профилей лаунчера GT:NH.

Каждый профиль описывает одну сборку / инстанс:
  - имя, папка игры, версия MC, версия пака
  - выделяемая память, путь к Java (опционально)
  - счётчик запусков и дата последней игры

Файл: profiles.json (рядом с settings.json)
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Optional


FORMAT_VERSION = 1

# Значения по умолчанию для нового профиля
_DEFAULTS: dict = {
    "name":         "GT New Horizons",
    "game_dir":     "",
    "java_path":    "",
    "memory_mb":    4096,
    "loader":       "forge-gtnh",
    "mc_version":   "1.7.10",
    "pack_version": "",
    "last_played":  None,
    "play_count":   0,
}


# ══════════════════════════════════════════════════════════════════════════════
class Profile:
    """Один игровой профиль."""

    def __init__(self, data: dict):
        self._d: dict = {**_DEFAULTS, **data}

    # ── Свойства ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._d["name"]

    @name.setter
    def name(self, v: str):
        self._d["name"] = v.strip()

    @property
    def game_dir(self) -> str:
        return self._d["game_dir"]

    @game_dir.setter
    def game_dir(self, v: str):
        self._d["game_dir"] = v

    @property
    def java_path(self) -> str:
        return self._d.get("java_path", "")

    @java_path.setter
    def java_path(self, v: str):
        self._d["java_path"] = v

    @property
    def memory_mb(self) -> int:
        try:
            return int(self._d.get("memory_mb", 4096))
        except (ValueError, TypeError):
            return 4096

    @memory_mb.setter
    def memory_mb(self, v: int):
        self._d["memory_mb"] = max(512, int(v))

    @property
    def loader(self) -> str:
        return self._d.get("loader", "forge-gtnh")

    @loader.setter
    def loader(self, v: str):
        self._d["loader"] = v

    @property
    def mc_version(self) -> str:
        return self._d.get("mc_version", "")

    @mc_version.setter
    def mc_version(self, v: str):
        self._d["mc_version"] = v

    @property
    def pack_version(self) -> str:
        return self._d.get("pack_version", "")

    @pack_version.setter
    def pack_version(self, v: str):
        self._d["pack_version"] = v

    @property
    def last_played(self) -> Optional[str]:
        return self._d.get("last_played")

    @property
    def play_count(self) -> int:
        try:
            return int(self._d.get("play_count", 0))
        except (ValueError, TypeError):
            return 0

    # ── Методы ────────────────────────────────────────────────────────────────

    def record_play(self):
        """Вызывается при каждом успешном запуске."""
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self._d["last_played"] = now
        self._d["play_count"]  = self.play_count + 1

    def subtitle(self) -> str:
        """Строка описания для UI (под именем профиля)."""
        parts: list[str] = []
        if self.mc_version:
            parts.append(f"MC {self.mc_version}")
        if self.pack_version:
            parts.append(f"v{self.pack_version}")
        if self.loader and self.loader not in ("forge-gtnh", ""):
            parts.append(self.loader)
        if self.play_count:
            parts.append(f"сыграно {self.play_count}×")
        return "  ·  ".join(parts) if parts else "Новый профиль"

    def to_dict(self) -> dict:
        return dict(self._d)

    def copy(self) -> "Profile":
        return Profile(dict(self._d))


# ══════════════════════════════════════════════════════════════════════════════
class ProfileManager:
    """
    Управляет списком профилей.
    Читает и записывает profiles.json.
    """

    def __init__(self, profiles_file: str, fallback_game_dir: str = ""):
        self._file     = profiles_file
        self._fallback = fallback_game_dir
        self._profiles: list[Profile] = []
        self._selected: str = ""
        self._load()

    # ── Загрузка / сохранение ─────────────────────────────────────────────────

    def _load(self):
        if os.path.isfile(self._file):
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._profiles = [Profile(p) for p in raw.get("profiles", [])]
                self._selected = raw.get("selected_profile", "")
            except Exception:
                pass

        # Если файл не существует или пустой — создаём профиль по умолчанию
        if not self._profiles:
            self._profiles = [Profile({
                "name":     "GT New Horizons",
                "game_dir": self._fallback,
                "memory_mb": 4096,
            })]

        # Защита: selected должен указывать на реальный профиль
        if self._selected not in self.names:
            self._selected = self._profiles[0].name

    def save(self):
        os.makedirs(os.path.dirname(self._file) or ".", exist_ok=True)
        data = {
            "format_version":   FORMAT_VERSION,
            "selected_profile": self._selected,
            "profiles":         [p.to_dict() for p in self._profiles],
        }
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── Свойства ──────────────────────────────────────────────────────────────

    @property
    def names(self) -> list[str]:
        return [p.name for p in self._profiles]

    @property
    def profiles(self) -> list[Profile]:
        return list(self._profiles)

    @property
    def selected_name(self) -> str:
        return self._selected

    @selected_name.setter
    def selected_name(self, name: str):
        if name in self.names:
            self._selected = name

    @property
    def selected(self) -> Profile:
        for p in self._profiles:
            if p.name == self._selected:
                return p
        return self._profiles[0]

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Profile]:
        for p in self._profiles:
            if p.name == name:
                return p
        return None

    def add(self, profile: Profile) -> bool:
        """Добавляет профиль. False если имя уже занято."""
        if profile.name in self.names:
            return False
        self._profiles.append(profile)
        return True

    def update(self, old_name: str, new_profile: Profile) -> bool:
        """Заменяет профиль old_name новым объектом. Синхронизирует selected."""
        for i, p in enumerate(self._profiles):
            if p.name == old_name:
                self._profiles[i] = new_profile
                if self._selected == old_name:
                    self._selected = new_profile.name
                return True
        return False

    def delete(self, name: str) -> bool:
        """Удаляет профиль. Нельзя удалить последний."""
        if len(self._profiles) <= 1:
            return False
        self._profiles = [p for p in self._profiles if p.name != name]
        if self._selected == name:
            self._selected = self._profiles[0].name
        return True

    def select(self, name: str):
        """Выбирает профиль и сохраняет файл."""
        self.selected_name = name
        self.save()
