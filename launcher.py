import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import subprocess
import json
import logging
import os
import platform
import sys
import urllib.request
import urllib.error
import zipfile
import tempfile

# ── Пути (работает и в .exe, и в режиме разработки) ───────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
    APP_DIR  = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(__file__)
    APP_DIR  = BASE_DIR

SETTINGS_FILE  = os.path.join(APP_DIR,  "settings.json")
PROFILES_FILE  = os.path.join(APP_DIR,  "profiles.json")
BRAND_FILE     = os.path.join(BASE_DIR, "brand.json")

# ── Лог-файл ────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=os.path.join(APP_DIR, "launcher.log"),
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)
log = logging.getLogger(__name__)

# ── brand.json ────────────────────────────────────────────────────────────────
_BRAND_DEFAULTS = {
    "server_name":       "My Server",
    "launcher_title":    "Minecraft Launcher",
    "launcher_version":  "1.0.0",
    "api_url":           "http://91.144.171.180:8000",
    "fixed_version":     "1.20.1-wm2",
    "client_download_url": "http://91.144.171.180:8000/api/downloads/client/latest",
    "color_dark":        "#1a1a2e",
    "color_panel":       "#16213e",
    "color_accent":      "#00d4aa",
    "color_red":         "#e94560",
    "color_input":       "#0f3460",
    "color_text":        "#e0e0e0",
    "color_muted":       "#888888",
    "default_memory":    "2048",
    "authlib_jar":       "",
    "authlib_server_url": "",
}

def _load_brand() -> dict:
    d = dict(_BRAND_DEFAULTS)
    if os.path.exists(BRAND_FILE):
        try:
            with open(BRAND_FILE, "r", encoding="utf-8") as f:
                d.update(json.load(f))
        except Exception:
            pass
    return d

BRAND        = _load_brand()
API_BASE     = BRAND["api_url"].rstrip("/")
FIXED_VER    = BRAND["fixed_version"]
DOWNLOAD_URL = BRAND["client_download_url"]
LIBS_URL     = BRAND.get("libs_download_url", "")

DARK   = BRAND["color_dark"]
PANEL  = BRAND["color_panel"]
ACCENT = BRAND["color_accent"]
RED    = BRAND["color_red"]
INPUT  = BRAND["color_input"]
TEXT   = BRAND["color_text"]
MUTED  = BRAND["color_muted"]


# ── Путь к папке .minecraft по умолчанию (без внешних зависимостей) ──────────
def _default_mc_dir() -> str:
    system = platform.system()
    if system == "Windows":
        return os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), ".minecraft"
        )
    if system == "Darwin":
        return os.path.expanduser(
            "~/Library/Application Support/minecraft"
        )
    return os.path.expanduser("~/.minecraft")


import gtnh_launch
import profiles as prof_mod
from profiles import Profile, ProfileManager


# ── API-запросы ───────────────────────────────────────────────────────────────

def _api_post(endpoint: str, payload: dict, timeout: int = 10) -> dict:
    """POST к API, возвращает распарсенный JSON-ответ."""
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{API_BASE}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _api_get(endpoint: str, token: str = "", timeout: int = 10) -> dict:
    """GET к API, опционально с Bearer-токеном."""
    req = urllib.request.Request(f"{API_BASE}{endpoint}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── Версия клиента ────────────────────────────────────────────────────────────
def _client_version_file(game_dir: str) -> str:
    return os.path.join(game_dir, ".launcher_client_version")

def _local_client_version(game_dir: str) -> str:
    try:
        return open(_client_version_file(game_dir)).read().strip()
    except Exception:
        return ""

def _save_client_version(game_dir: str, ver: str):
    with open(_client_version_file(game_dir), "w") as f:
        f.write(ver)


# ══════════════════════════════════════════════════════════════════════════════
class Settings:
    """Глобальные настройки (не зависящие от профиля)."""
    _defaults = {
        "java_path":   "",           # глобальный путь к Java (override профиля)
        "remember_me": False,
        "saved_login": "",
        "saved_token": "",           # API-токен сессии (не пароль!)
        # Поля ниже — только для миграции из старых settings.json:
        # "max_memory" и "game_dir" переезжают в profiles.json
    }

    def __init__(self):
        self.data = dict(self._defaults)
        self._migrated_game_dir   = ""
        self._migrated_memory     = BRAND["default_memory"]

        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded.pop("saved_password", None)
                # Запоминаем старые поля для последующей миграции в профиль
                self._migrated_game_dir = loaded.pop("game_dir", "")
                self._migrated_memory   = str(loaded.pop("max_memory",
                                                          BRAND["default_memory"]))
                self.data.update(loaded)
            except Exception:
                pass

    def save(self):
        # Сохраняем только глобальные поля (без game_dir / max_memory)
        out = {k: v for k, v in self.data.items()
               if k in self._defaults}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    def get(self, k, default=None):
        return self.data.get(k, self._defaults.get(k, default))

    def __getitem__(self, k):
        return self.data.get(k, self._defaults.get(k, ""))

    def __setitem__(self, k, v):
        self.data[k] = v


# ══════════════════════════════════════════════════════════════════════════════
class MinecraftLauncher:

    def __init__(self):
        self.cfg   = Settings()
        self._user = None        # dict после успешного входа
        self._busy = False

        # ── Профили ───────────────────────────────────────────────────────────
        # Если profiles.json ещё нет — создаём с данными из старого settings.json
        self.pm = ProfileManager(
            PROFILES_FILE,
            fallback_game_dir=self.cfg._migrated_game_dir or _default_mc_dir(),
        )
        self._migrate_settings_to_profile()

        self.root = tk.Tk()
        self.root.title(BRAND["launcher_title"])
        self.root.resizable(False, False)
        self.root.configure(bg=DARK)

        icon = os.path.join(BASE_DIR, "icon.ico")
        if os.path.isfile(icon):
            try:
                self.root.iconbitmap(icon)
            except Exception:
                pass

        self._apply_style()
        self._show_login()

    # ── Миграция старых настроек → первый профиль ─────────────────────────────

    def _migrate_settings_to_profile(self):
        """
        Если profiles.json создан впервые (из fallback) и в старом settings.json
        были game_dir / max_memory — переносим их в профиль по умолчанию.
        Запускается один раз; при следующем старте profiles.json уже существует.
        """
        if os.path.isfile(PROFILES_FILE):
            return   # файл уже существует — миграция не нужна

        p = self.pm.selected
        if self.cfg._migrated_game_dir:
            p.game_dir = self.cfg._migrated_game_dir
        if self.cfg._migrated_memory:
            try:
                p.memory_mb = int(self.cfg._migrated_memory)
            except ValueError:
                pass
        self.pm.save()

    # ── Общий стиль ttk ───────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Horizontal.TProgressbar",
                     troughcolor=PANEL, background=ACCENT, thickness=4)
        s.configure("TCombobox",
                     fieldbackground=INPUT, background=INPUT,
                     foreground=TEXT, selectbackground=INPUT,
                     selectforeground=TEXT, arrowcolor=ACCENT)
        s.map("TCombobox", fieldbackground=[("readonly", INPUT)],
              foreground=[("readonly", TEXT)])

    # ═══════════ ЭКРАН ВХОДА ══════════════════════════════════════════════════

    def _show_login(self):
        self._clear_window()
        self.root.geometry("440x370")
        self._center()

        wrap = tk.Frame(self.root, bg=DARK)
        wrap.place(relx=.5, rely=.5, anchor="center")

        # Логотип
        tk.Label(wrap, text="⛏", bg=DARK, fg=ACCENT,
                 font=("Segoe UI", 36)).pack()
        tk.Label(wrap, text=BRAND["server_name"], bg=DARK, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(pady=(0, 2))
        tk.Label(wrap, text="Войдите, чтобы продолжить",
                 bg=DARK, fg=MUTED, font=("Segoe UI", 9)).pack(pady=(0, 20))

        # Логин
        self._lbl(wrap, "Логин").pack(anchor="w")
        self.login_var = tk.StringVar()
        le = self._ent(wrap, self.login_var)
        le.pack(pady=(2, 10))
        le.focus_set()

        # Пароль
        self._lbl(wrap, "Пароль").pack(anchor="w")
        self.pass_var = tk.StringVar()
        pe = self._ent(wrap, self.pass_var)
        pe.config(show="●")
        pe.pack(pady=(2, 6))
        pe.bind("<Return>", lambda _: self._on_login())

        # Ошибка
        self.login_err = tk.Label(wrap, text="", bg=DARK, fg=RED,
                                   font=("Segoe UI", 9))
        self.login_err.pack(pady=2)

        # Кнопка + прогресс
        self.login_btn = self._btn(wrap, "  Войти  ", ACCENT,
                                    self._on_login, fg=DARK)
        self.login_btn.pack(pady=8)

        self.login_pb = ttk.Progressbar(wrap, mode="indeterminate",
                                         style="Horizontal.TProgressbar",
                                         length=220)
        self.login_pb.pack()

        # Запомнить меня
        self.remember_var = tk.BooleanVar(value=self.cfg["remember_me"])
        tk.Checkbutton(wrap, text="Запомнить меня",
                       variable=self.remember_var,
                       bg=DARK, fg=MUTED, selectcolor=INPUT,
                       activebackground=DARK,
                       font=("Segoe UI", 9)).pack(pady=(4, 0))

        tk.Label(wrap,
                 text="Нет аккаунта? Напишите боту в Telegram",
                 bg=DARK, fg=MUTED, font=("Segoe UI", 8)).pack(pady=(10, 0))

        # Автовход по сохранённому токену
        if (self.cfg["remember_me"]
                and self.cfg["saved_login"]
                and self.cfg["saved_token"]):
            login = self.cfg["saved_login"]
            self.login_var.set(login)
            self.login_err.config(text=f"Вход как {login}...", fg=MUTED)
            self.root.after(600, self._on_token_autologin)

    # ── Вход по паролю ────────────────────────────────────────────────────────

    def _on_login(self):
        if self._busy:
            return
        login    = self.login_var.get().strip()
        password = self.pass_var.get()
        if not login:
            self.login_err.config(text="Введите логин")
            return
        if not password:
            self.login_err.config(text="Введите пароль")
            return
        self.login_err.config(text="")
        self._set_busy(True, pb=self.login_pb)
        self.login_btn.config(state="disabled")
        threading.Thread(target=self._do_login,
                          args=(login, password), daemon=True).start()

    def _do_login(self, login: str, password: str):
        try:
            data = _api_post("/api/login", {"login": login, "password": password})

            if data.get("success"):
                token = data.get("token", "")
                self._user = {"login": data.get("login", login), "token": token}
                self._save_credentials(login, token)
                self.root.after(0, self._show_main)
            else:
                err = data.get("error", "Неверный логин или пароль")
                self.root.after(0, lambda: self.login_err.config(text=err))

        except urllib.error.HTTPError as e:
            msgs = {401: "Неверный логин или пароль",
                    403: "Аккаунт заблокирован",
                    429: "Слишком много попыток — подождите"}
            msg = msgs.get(e.code, f"Ошибка сервера: {e.code}")
            self.root.after(0, lambda m=msg: self.login_err.config(text=m))
        except urllib.error.URLError:
            self.root.after(0, lambda: self.login_err.config(
                text="Нет соединения с сервером"))
        except Exception as e:
            log.exception("Ошибка входа")
            self.root.after(0, lambda: self.login_err.config(
                text=f"Ошибка: {e}"))
        finally:
            self._set_busy(False, pb=self.login_pb)
            self.root.after(0, lambda: self.login_btn.config(state="normal"))

    # ── Автовход по токену ────────────────────────────────────────────────────

    def _on_token_autologin(self):
        if self._busy:
            return
        self._set_busy(True, pb=self.login_pb)
        token = self.cfg["saved_token"]
        login = self.cfg["saved_login"]
        threading.Thread(target=self._do_token_login,
                          args=(login, token), daemon=True).start()

    def _do_token_login(self, login: str, token: str):
        try:
            data = _api_get("/api/me", token=token)
            self._user = {"login": data.get("login", login), "token": token}
            self.root.after(0, self._show_main)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Токен устарел — сбрасываем, оставляем логин в поле
                self.cfg["saved_token"] = ""
                self.cfg.save()
                self.root.after(0, lambda: self.login_err.config(
                    text="Сессия истекла, введите пароль", fg=RED))
            else:
                self.root.after(0, lambda: self.login_err.config(
                    text=f"Ошибка сервера: {e.code}", fg=RED))
        except Exception:
            # Нет связи — сбрасываем автовход, показываем поле пароля
            self.cfg["saved_token"] = ""
            self.cfg.save()
            self.root.after(0, lambda: self.login_err.config(
                text="Нет связи с сервером, введите пароль", fg=MUTED))
        finally:
            self._set_busy(False, pb=self.login_pb)
            self.root.after(0, lambda: self.login_btn.config(state="normal"))

    # ═══════════ ГЛАВНЫЙ ЭКРАН ════════════════════════════════════════════════

    def _show_main(self):
        self._clear_window()
        self.root.geometry("700x560")
        self._center()

        username = self._user.get("login", "?")

        # ── Шапка ──────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=PANEL, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text=f"⛏  {BRAND['server_name']}",
                 bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 17, "bold")).pack(side="left", padx=18)
        tk.Label(hdr, text=f"v{BRAND['launcher_version']}",
                 bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left")

        right_hdr = tk.Frame(hdr, bg=PANEL)
        right_hdr.pack(side="right", padx=12)
        tk.Label(right_hdr, text=f"👤  {username}",
                 bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        self._sbtn(right_hdr, "Выйти", self._on_logout).pack(side="left")

        # ── Тело ───────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=DARK)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # Левая панель
        left = tk.Frame(body, bg=DARK, width=240)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # ── Блок профилей ─────────────────────────────────────────────────
        tk.Label(left, text="Профиль", bg=DARK, fg=TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w")

        pf = tk.Frame(left, bg=DARK)
        pf.pack(anchor="w", pady=(3, 4), fill="x")

        self.profile_var = tk.StringVar(value=self.pm.selected_name)
        self.profile_cb  = ttk.Combobox(
            pf, textvariable=self.profile_var,
            values=self.pm.names,
            state="readonly", width=15,
            font=("Segoe UI", 10),
        )
        self.profile_cb.pack(side="left")
        self.profile_cb.bind("<<ComboboxSelected>>", self._on_profile_change)

        self._sbtn(pf, "✚", self._on_profile_new).pack(side="left", padx=(4, 1))
        self._sbtn(pf, "✎", self._on_profile_edit).pack(side="left", padx=1)
        self._sbtn(pf, "✕", self._on_profile_delete).pack(side="left", padx=1)

        # Подпись профиля (MC-версия, пак, счётчик)
        self.profile_sub = tk.Label(left, text=self.pm.selected.subtitle(),
                                     bg=DARK, fg=MUTED,
                                     font=("Segoe UI", 8), wraplength=220,
                                     justify="left")
        self.profile_sub.pack(anchor="w", pady=(0, 12))

        # ── Память ────────────────────────────────────────────────────────
        tk.Label(left, text="Память (МБ)", bg=DARK, fg=TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w")
        self.memory_var = tk.StringVar(value=str(self.pm.selected.memory_mb))
        self._ent(left, self.memory_var, w=14).pack(anchor="w", pady=(3, 14))

        # ── Папка игры ────────────────────────────────────────────────────
        tk.Label(left, text="Папка игры", bg=DARK, fg=TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w")
        gf = tk.Frame(left, bg=DARK)
        gf.pack(anchor="w", pady=(3, 20))
        self.gamedir_var = tk.StringVar(value=self.pm.selected.game_dir)
        self._ent(gf, self.gamedir_var, w=16).pack(side="left")
        self._sbtn(gf, "…", self._browse_gamedir).pack(side="left", padx=3)

        # Прогресс загрузки
        self.dl_label = tk.Label(left, text="", bg=DARK, fg=MUTED,
                                  font=("Segoe UI", 8), wraplength=220,
                                  justify="left")
        self.dl_label.pack(anchor="w", pady=(0, 4))

        self.dl_pb = ttk.Progressbar(left, style="Horizontal.TProgressbar",
                                      length=220, mode="determinate",
                                      maximum=100)
        self.dl_pb.pack(anchor="w", pady=(0, 12))

        # Кнопки
        self.install_btn = self._btn(
            left, "⬇  Установить / Обновить", RED, self._on_install)
        self.install_btn.pack(fill="x", pady=3)

        self.libs_btn = self._btn(
            left, "🔧  Обновить библиотеки", INPUT, self._on_install_libs)
        self.libs_btn.pack(fill="x", pady=3)

        self.play_btn = self._btn(
            left, "▶  Играть", ACCENT, self._on_launch, fg=DARK)
        self.play_btn.pack(fill="x", pady=3)

        # Правая панель — новости
        right = tk.Frame(body, bg=PANEL)
        right.pack(side="right", fill="both", expand=True, padx=(16, 0))

        tk.Label(right, text="  Новости", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 4))
        tk.Frame(right, bg=ACCENT, height=1).pack(fill="x", padx=10)

        self.news_scroll = tk.Frame(right, bg=PANEL)
        self.news_scroll.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(self.news_scroll, text="Загрузка...",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack()

        # ── Статус-бар ─────────────────────────────────────────────────────
        foot = tk.Frame(self.root, bg=PANEL)
        foot.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Готово")
        tk.Label(foot, textvariable=self.status_var,
                 bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=4)

        # Фоновые задачи при запуске
        threading.Thread(target=self._fetch_news, daemon=True).start()
        threading.Thread(target=self._check_client_update, daemon=True).start()

    # ── Новости ───────────────────────────────────────────────────────────────

    def _fetch_news(self):
        try:
            token = self._user.get("token", "") if self._user else ""
            items = _api_get("/api/news", token=token, timeout=6)
            self.root.after(0, self._render_news, items)
        except Exception:
            self.root.after(0, self._render_news, [])

    def _render_news(self, items: list):
        for w in self.news_scroll.winfo_children():
            w.destroy()
        if not items:
            tk.Label(self.news_scroll, text="Нет новостей",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack()
            return
        for item in items[:6]:
            card = tk.Frame(self.news_scroll, bg=INPUT, pady=6, padx=10)
            card.pack(fill="x", pady=3)
            title = item.get("title", "")
            body  = item.get("body",  "")
            if title:
                tk.Label(card, text=title, bg=INPUT, fg=ACCENT,
                         font=("Segoe UI", 10, "bold"),
                         wraplength=320, justify="left").pack(anchor="w")
            if body:
                tk.Label(card,
                         text=body[:160] + ("…" if len(body) > 160 else ""),
                         bg=INPUT, fg=TEXT,
                         font=("Segoe UI", 9),
                         wraplength=320, justify="left").pack(anchor="w", pady=(2, 0))

    # ── Проверка версии клиента ───────────────────────────────────────────────

    def _check_client_update(self):
        game_dir = self.gamedir_var.get() or self.pm.selected.game_dir or _default_mc_dir()
        local    = _local_client_version(game_dir)
        try:
            data   = _api_get("/api/client/version", timeout=5)
            remote = data.get("version", FIXED_VER)
        except Exception:
            remote = FIXED_VER

        if local != remote or not self._client_installed(game_dir):
            self.root.after(0, self._set_status,
                             f"Доступно обновление клиента ({remote}). "
                             "Нажмите «Установить / Обновить».")
        else:
            self.root.after(0, self._set_status, "Клиент актуален.")

    def _client_installed(self, game_dir: str) -> bool:
        return gtnh_launch.is_installed(game_dir, game_dir)

    # ── Установка / обновление клиента ───────────────────────────────────────

    def _on_install(self):
        if self._busy:
            return
        game_dir = self.gamedir_var.get() or _default_mc_dir()
        threading.Thread(target=self._do_install,
                          args=(game_dir,), daemon=True).start()

    def _do_install(self, game_dir: str):
        self._set_busy(True)
        os.makedirs(game_dir, exist_ok=True)

        tmp_path = None
        try:
            # ── 1. Скачать zip сборки ──────────────────────────────────────
            self.root.after(0, self._set_status, "Соединение с сервером...")

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            os.close(tmp_fd)

            def _reporthook(blocks: int, block_size: int, total: int):
                if total > 0:
                    pct      = min(blocks * block_size * 100 // total, 100)
                    mb_done  = blocks * block_size / 1_048_576
                    mb_total = total / 1_048_576
                    self.root.after(0, self._dl_progress,
                                     pct,
                                     f"Загрузка: {mb_done:.1f} / {mb_total:.1f} МБ")

            self.root.after(0, self._dl_progress, 0, "Начало загрузки...")
            urllib.request.urlretrieve(DOWNLOAD_URL, tmp_path, _reporthook)
            self.root.after(0, self._dl_progress, 100, "Загрузка завершена")

            # ── 2. Распаковать в папку игры ────────────────────────────────
            self.root.after(0, self._set_status, "Распаковка сборки...")
            self.root.after(0, self._dl_progress, 0, "Распаковка...")

            with zipfile.ZipFile(tmp_path, "r") as zf:
                names = zf.namelist()
                total = len(names)
                for i, name in enumerate(names, 1):
                    zf.extract(name, game_dir)
                    if i % 50 == 0 or i == total:
                        pct = i * 100 // total
                        self.root.after(0, self._dl_progress,
                                         pct, f"Распаковка: {i}/{total}")

            os.unlink(tmp_path)
            tmp_path = None

            # ── 3. Скачать MC jar, библиотеки, ассеты ─────────────────────
            def _on_status(msg):
                self.root.after(0, self._set_status, msg)

            def _on_progress(done, total_n):
                if total_n > 0:
                    pct = min(done * 100 // total_n, 100)
                    self.root.after(0, self._dl_progress,
                                     pct, f"{done}/{total_n}")

            gtnh_launch.install_dependencies(
                instance_dir=game_dir,
                shared_dir=game_dir,
                on_status=_on_status,
                on_progress=_on_progress,
                libs_url=LIBS_URL,
            )

            # ── 4. Запомнить версию ────────────────────────────────────────
            _save_client_version(game_dir, FIXED_VER)

            self.root.after(0, self._dl_progress, 100, "Готово!")
            self.root.after(0, self._set_status, "Сборка установлена!")
            self.root.after(0, messagebox.showinfo,
                             "Готово", "Сборка успешно установлена!")

        except Exception as e:
            log.exception("Ошибка установки")
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            self.root.after(0, messagebox.showerror, "Ошибка установки", str(e))
            self.root.after(0, self._set_status, "Ошибка при установке")
            self.root.after(0, self._dl_progress, 0, "")
        finally:
            self._set_busy(False)

    def _dl_progress(self, pct: int, label: str):
        self.dl_pb["value"] = pct
        self.dl_label.config(text=label)

    # ── Обновление только библиотек ───────────────────────────────────────────

    def _on_install_libs(self):
        """Скачивает/обновляет библиотеки без переустановки всей сборки."""
        if self._busy:
            return
        game_dir = self.gamedir_var.get() or _default_mc_dir()
        if not gtnh_launch.find_instance_dir(game_dir):
            messagebox.showerror("Ошибка",
                                  "Сборка не найдена. Сначала нажмите «Установить».")
            return
        threading.Thread(target=self._do_install_libs,
                          args=(game_dir,), daemon=True).start()

    def _do_install_libs(self, game_dir: str):
        self._set_busy(True)
        try:
            def _on_status(msg):
                self.root.after(0, self._set_status, msg)

            def _on_progress(done, total_n):
                if total_n > 0:
                    pct = min(done * 100 // total_n, 100)
                    self.root.after(0, self._dl_progress, pct, f"{done}/{total_n}")

            gtnh_launch.install_dependencies(
                instance_dir=game_dir,
                shared_dir=game_dir,
                on_status=_on_status,
                on_progress=_on_progress,
                libs_url=LIBS_URL,
            )
            self.root.after(0, self._dl_progress, 100, "Готово!")
            self.root.after(0, self._set_status, "Библиотеки обновлены!")
        except Exception as e:
            log.exception("Ошибка обновления библиотек")
            self.root.after(0, messagebox.showerror, "Ошибка", str(e))
            self.root.after(0, self._set_status, "Ошибка обновления библиотек")
        finally:
            self._set_busy(False)

    # ── Запуск игры ───────────────────────────────────────────────────────────

    def _on_launch(self):
        if self._busy:
            return
        game_dir = self.gamedir_var.get() or _default_mc_dir()
        username = self._user.get("login", "Player")

        if not self._client_installed(game_dir):
            if messagebox.askyesno(
                    "Клиент не найден",
                    "Сборка не установлена. Установить сейчас?"):
                self._on_install()
            return

        # Сохраняем изменения в профиль
        p = self.pm.selected
        try:
            p.memory_mb = int(self.memory_var.get())
        except ValueError:
            pass
        p.game_dir = game_dir
        self.pm.save()
        self.cfg.save()

        threading.Thread(target=self._do_launch,
                          args=(username, game_dir), daemon=True).start()

    def _do_launch(self, username: str, game_dir: str):
        self._set_busy(True)
        self.root.after(0, self._set_status, "Запуск игры...")

        java = self.cfg["java_path"].strip()
        if not java or not os.path.isfile(java):
            java = "java"

        try:
            cmd = gtnh_launch.build_command(
                instance_dir=game_dir,
                shared_dir=game_dir,
                username=username,
                memory_mb=int(self.memory_var.get()),
                java_path=java,
            )

            inst_dir = gtnh_launch.find_instance_dir(game_dir) or game_dir
            mc_dir   = os.path.join(inst_dir, ".minecraft")
            os.makedirs(mc_dir, exist_ok=True)   # создаём если нет

            proc = subprocess.Popen(
                cmd, cwd=mc_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # stdout + stderr в одном потоке
            )

        except FileNotFoundError:
            self.root.after(0, self._show_launch_error,
                             "Java не найдена!\n\nУстановите Java 17+ и укажите путь в настройках.")
            self.root.after(0, self._set_status, "Java не найдена")
            self._set_busy(False)
            return
        except RuntimeError as e:
            self.root.after(0, self._show_launch_error, str(e))
            self.root.after(0, self._set_status, "Ошибка запуска")
            self._set_busy(False)
            return
        except Exception as e:
            log.exception("Ошибка при построении команды запуска")
            self.root.after(0, messagebox.showerror, "Ошибка запуска", str(e))
            self.root.after(0, self._set_status, "Ошибка при запуске")
            self._set_busy(False)
            return

        # Процесс запущен — освобождаем UI сразу
        self._set_busy(False)
        self.root.after(0, self._set_status, "Игра запущена!")

        # Мониторинг в фоне — поймает краш даже через несколько минут
        threading.Thread(target=self._monitor_game,
                          args=(proc,), daemon=True).start()

    def _monitor_game(self, proc: subprocess.Popen):
        """Ждёт завершения игры и показывает ошибку при ненулевом коде."""
        # Считаем запуск состоявшимся — сразу записываем в профиль
        self.pm.selected.record_play()
        self.pm.save()

        try:
            out_b, _ = proc.communicate()   # блокируемся до завершения
            ret = proc.returncode

            def _decode(b: bytes) -> str:
                if not b:
                    return ""
                for enc in ("utf-8", "cp1251", "latin-1"):
                    try:
                        return b.decode(enc)
                    except Exception:
                        pass
                return b.decode("utf-8", errors="replace")

            if ret != 0:
                err_text = _decode(out_b).strip() or f"Процесс завершился с кодом {ret}"
                log.warning("Игра завершилась с кодом %d:\n%s", ret, err_text[:2000])
                self.root.after(0, self._show_launch_error, err_text)
                self.root.after(0, self._set_status, "Игра завершилась с ошибкой")
            else:
                self.root.after(0, self._set_status, "Игра завершена")
        except Exception as e:
            log.exception("Ошибка мониторинга игры")
            self.root.after(0, self._set_status, f"Мониторинг: {e}")

    # ── Окно с ошибкой запуска ────────────────────────────────────────────────

    def _show_launch_error(self, text: str):
        win = tk.Toplevel(self.root)
        win.title("Ошибка запуска")
        win.geometry("640x420")
        win.configure(bg=DARK)
        win.grab_set()

        tk.Label(win, text="Игра завершилась с ошибкой:",
                 bg=DARK, fg=RED, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=14, pady=(14, 4))

        frame = tk.Frame(win, bg=DARK)
        frame.pack(fill="both", expand=True, padx=14, pady=4)

        sb  = tk.Scrollbar(frame)
        sb.pack(side="right", fill="y")
        box = tk.Text(frame, bg=INPUT, fg=TEXT, font=("Consolas", 9),
                      relief="flat", wrap="word", yscrollcommand=sb.set)
        box.pack(side="left", fill="both", expand=True)
        sb.config(command=box.yview)

        box.insert("1.0", text)
        box.config(state="disabled")

        bf = tk.Frame(win, bg=DARK)
        bf.pack(fill="x", padx=14, pady=10)

        def _copy():
            win.clipboard_clear()
            win.clipboard_append(text)

        self._sbtn(bf, "Копировать", _copy).pack(side="left")
        self._btn(bf, "Закрыть", RED, win.destroy).pack(side="right")

    # ══════════ ПРОФИЛИ ═══════════════════════════════════════════════════════

    def _refresh_profile_ui(self):
        """Синхронизирует комбобокс, поля памяти/папки и подпись с pm.selected."""
        p = self.pm.selected
        self.profile_cb.config(values=self.pm.names)
        self.profile_var.set(p.name)
        self.memory_var.set(str(p.memory_mb))
        self.gamedir_var.set(p.game_dir)
        self.profile_sub.config(text=p.subtitle())

    def _on_profile_change(self, _event=None):
        """Пользователь выбрал другой профиль из комбобокса."""
        name = self.profile_var.get()
        self.pm.select(name)          # select() вызывает save()
        self._refresh_profile_ui()

    def _on_profile_new(self):
        """Создать новый профиль."""
        new_p = Profile({
            "name":     "Новый профиль",
            "game_dir": _default_mc_dir(),
            "memory_mb": int(BRAND["default_memory"]),
        })
        result = self._open_profile_dialog(new_p, title="Новый профиль")
        if result is None:
            return
        if not self.pm.add(result):
            messagebox.showerror("Ошибка", f'Профиль «{result.name}» уже существует.')
            return
        self.pm.select(result.name)
        self.pm.save()
        self._refresh_profile_ui()

    def _on_profile_edit(self):
        """Редактировать текущий профиль."""
        p      = self.pm.selected.copy()
        old    = p.name
        result = self._open_profile_dialog(p, title=f"Редактировать: {old}")
        if result is None:
            return
        # Проверяем конфликт имён (кроме самого себя)
        if result.name != old and result.name in self.pm.names:
            messagebox.showerror("Ошибка",
                                  f'Профиль «{result.name}» уже существует.')
            return
        self.pm.update(old, result)
        self.pm.save()
        self._refresh_profile_ui()

    def _on_profile_delete(self):
        """Удалить текущий профиль."""
        name = self.pm.selected_name
        if len(self.pm.names) <= 1:
            messagebox.showinfo("Нельзя удалить", "Должен оставаться хотя бы один профиль.")
            return
        if not messagebox.askyesno("Удалить профиль",
                                    f'Удалить профиль «{name}»?\nЭто не удалит файлы игры.'):
            return
        self.pm.delete(name)
        self.pm.save()
        self._refresh_profile_ui()

    def _open_profile_dialog(self, p: Profile, title: str) -> "Profile | None":
        """
        Модальный диалог редактирования профиля.
        Возвращает изменённый Profile или None если пользователь нажал Отмена.
        """
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("420x380")
        win.configure(bg=DARK)
        win.resizable(False, False)
        win.grab_set()

        result_holder: list[Profile] = []   # [0] = Profile если OK

        # ── Поля ──────────────────────────────────────────────────────────
        fields: list[tuple[str, tk.StringVar, str]] = []

        def row(label: str, value: str, hint: str = "") -> tk.StringVar:
            tk.Label(win, text=label, bg=DARK, fg=TEXT,
                     font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(10, 0))
            var = tk.StringVar(value=value)
            ent = self._ent(win, var, w=38)
            ent.pack(anchor="w", padx=18, pady=(2, 0))
            if hint:
                tk.Label(win, text=hint, bg=DARK, fg=MUTED,
                         font=("Segoe UI", 8)).pack(anchor="w", padx=18)
            fields.append((label, var, hint))
            return var

        v_name    = row("Название",      p.name)
        v_dir     = row("Папка игры",    p.game_dir,    "Папка с mmc-pack.json")
        v_memory  = row("Память (МБ)",   str(p.memory_mb), "Рекомендуется 4096–8192")
        v_java    = row("Java (путь)",   p.java_path,   "Оставьте пустым для авто-поиска")
        v_mcver   = row("Версия MC",     p.mc_version)
        v_packver = row("Версия пака",   p.pack_version)

        # Кнопка «Обзор» для папки игры
        def _browse():
            d = filedialog.askdirectory(title="Папка для игры", parent=win)
            if d:
                v_dir.set(d)

        br_frame = tk.Frame(win, bg=DARK)
        br_frame.pack(anchor="w", padx=18, pady=(0, 4))
        self._sbtn(br_frame, "📂 Обзор", _browse).pack(side="left")

        # ── Кнопки OK / Отмена ────────────────────────────────────────────
        def _ok():
            name = v_name.get().strip()
            if not name:
                messagebox.showerror("Ошибка", "Название профиля не может быть пустым.", parent=win)
                return
            try:
                mem = int(v_memory.get())
                if mem < 256:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Ошибка", "Память должна быть числом ≥ 256.", parent=win)
                return

            p.name        = name
            p.game_dir    = v_dir.get().strip()
            p.memory_mb   = mem
            p.java_path   = v_java.get().strip()
            p.mc_version  = v_mcver.get().strip()
            p.pack_version = v_packver.get().strip()
            result_holder.append(p)
            win.destroy()

        def _cancel():
            win.destroy()

        btn_row = tk.Frame(win, bg=DARK)
        btn_row.pack(side="bottom", fill="x", padx=18, pady=14)
        self._btn(btn_row, "Сохранить", ACCENT, _ok, fg=DARK).pack(side="right", padx=(6, 0))
        self._sbtn(btn_row, "Отмена",   _cancel).pack(side="right")

        win.bind("<Return>", lambda _: _ok())
        win.bind("<Escape>", lambda _: _cancel())
        win.wait_window()

        return result_holder[0] if result_holder else None

    # ── Сохранение / сброс учётных данных ────────────────────────────────────

    def _save_credentials(self, login: str, token: str):
        remember = getattr(self, "remember_var", None)
        if remember and remember.get():
            self.cfg["remember_me"] = True
            self.cfg["saved_login"] = login
            self.cfg["saved_token"] = token
        else:
            self.cfg["remember_me"] = False
            self.cfg["saved_login"] = ""
            self.cfg["saved_token"] = ""
        self.cfg.save()

    def _on_logout(self):
        self.cfg["remember_me"] = False
        self.cfg["saved_login"] = ""
        self.cfg["saved_token"] = ""
        self.cfg.save()
        self._user = None
        self._show_login()

    # ── Браузер папок ─────────────────────────────────────────────────────────

    def _browse_gamedir(self):
        p = filedialog.askdirectory(title="Папка для Minecraft")
        if p:
            self.gamedir_var.set(p)

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def _clear_window(self):
        for w in self.root.winfo_children():
            w.destroy()

    def _center(self):
        self.root.update_idletasks()
        w  = self.root.winfo_width()
        h  = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    def _set_status(self, text: str):
        if hasattr(self, "status_var"):
            self.status_var.set(text)

    def _set_busy(self, busy: bool, pb: "ttk.Progressbar | None" = None):
        self._busy = busy
        target = pb or getattr(self, "dl_pb", None)
        if target:
            if busy and target["mode"] == "indeterminate":
                target.start(12)
            elif not busy and target["mode"] == "indeterminate":
                target.stop()

    # ── Виджеты-хелперы ───────────────────────────────────────────────────────

    def _lbl(self, p, text):
        return tk.Label(p, text=text, bg=DARK, fg=TEXT,
                         font=("Segoe UI", 10))

    def _ent(self, p, var, w=22):
        return tk.Entry(p, textvariable=var, width=w,
                         bg=INPUT, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=("Segoe UI", 10),
                         highlightthickness=1,
                         highlightcolor=ACCENT,
                         highlightbackground=PANEL)

    def _btn(self, p, text, bg, cmd, fg=TEXT):
        return tk.Button(p, text=text, bg=bg, fg=fg,
                          font=("Segoe UI", 11, "bold"),
                          relief="flat", padx=16, pady=8,
                          cursor="hand2", activebackground=bg,
                          command=cmd)

    def _sbtn(self, p, text, cmd):
        return tk.Button(p, text=text, bg=INPUT, fg=TEXT,
                          font=("Segoe UI", 9), relief="flat",
                          padx=8, pady=4, cursor="hand2", command=cmd)

    # ── Запуск ────────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    MinecraftLauncher().run()
