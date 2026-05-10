"""
Модуль запуска GT:NH (и любого MultiMC-инстанса с LWJGL3ify).
Читает patches/*.json, скачивает библиотеки/ассеты, строит java-команду.
"""

import json
import logging
import os
import pathlib
import platform
import tempfile
import urllib.request
import urllib.error
import zipfile

log = logging.getLogger(__name__)


# ── Дополнительные папки библиотек из других лаунчеров ────────────────────
def _extra_lib_dirs() -> list:
    """
    Возвращает список папок libraries/ из Prism, MultiMC и стандартного .minecraft.
    Используется как fallback при поиске jar-файлов.
    """
    dirs = []
    appdata = os.environ.get("APPDATA", "")
    home    = os.path.expanduser("~")

    candidates = [
        # Prism Launcher (Windows)
        os.path.join(appdata, "PrismLauncher", "libraries"),
        # MultiMC (Windows)
        os.path.join(appdata, "MultiMC", "libraries"),
        # GDLauncher / CurseForge
        os.path.join(appdata, "com.modrinth.theseus", "meta", "libraries"),
        # ATLauncher
        os.path.join(home, "ATLauncher", "libraries"),
        # Стандартный .minecraft
        os.path.join(appdata, ".minecraft", "libraries"),
    ]

    for d in candidates:
        if os.path.isdir(d):
            dirs.append(d)
    return dirs

_EXTRA_LIB_DIRS: list = _extra_lib_dirs()


# ── Репозитории Maven (в порядке приоритета) ───────────────────────────────
MAVEN_REPOS = [
    "https://libraries.minecraft.net/",
    "https://maven.minecraftforge.net/",
    "https://files.prismlauncher.org/maven/",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Утилиты
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_os() -> str:
    s = platform.system().lower()
    return "windows" if s == "windows" else ("osx" if s == "darwin" else "linux")


def _get_arch() -> str:
    m = platform.machine().lower()
    if "arm64" in m or "aarch64" in m:
        return "arm64"
    if "arm" in m:
        return "arm32"
    return "64" if platform.architecture()[0] == "64bit" else "32"


def _rule_ok(rules: list) -> bool:
    """Проверяет, нужна ли библиотека на текущей ОС."""
    if not rules:
        return True
    os_name = _get_os()
    result = False
    for r in rules:
        action  = r["action"]
        os_rule = r.get("os", {})
        match   = (not os_rule) or os_rule.get("name", "") == os_name
        if action == "allow" and match:
            result = True
        elif action == "disallow" and match:
            result = False
    return result


def _maven_to_rel(name: str) -> str:
    """
    com.google.guava:guava:15.0         -> com/google/guava/guava/15.0/guava-15.0.jar
    net.foo:bar:1.0:classifier           -> net/foo/bar/1.0/bar-1.0-classifier.jar
    """
    parts     = name.split(":")
    group     = parts[0].replace(".", "/")
    artifact  = parts[1]
    version   = parts[2]
    clf       = parts[3] if len(parts) > 3 else None
    fname     = f"{artifact}-{version}" + (f"-{clf}" if clf else "") + ".jar"
    return f"{group}/{artifact}/{version}/{fname}"


def _download(url: str, dest: str, on_progress=None) -> bool:
    """Скачивает файл, возвращает True при успехе."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    try:
        def _hook(b, bs, total):
            if on_progress and total > 0:
                on_progress(min(b * bs, total), total)
        urllib.request.urlretrieve(url, tmp, _hook)
        os.replace(tmp, dest)
        return True
    except Exception as exc:
        log.warning("Не удалось скачать %s → %s: %s", url, dest, exc)
        for p in (tmp, dest):
            try:
                os.unlink(p)
            except OSError:
                pass
        return False


def _resolve_lib(lib: dict, libs_dir: str, local_libs_dir: str,
                  on_status=None) -> "str | None":
    """
    Скачивает (если нужно) одну библиотеку и возвращает путь к jar.
    Возвращает None если библиотека не нужна на текущей ОС или это natives.
    """
    if not _rule_ok(lib.get("rules", [])):
        return None
    if lib.get("natives"):           # natives обрабатываются отдельно
        return None

    name = lib["name"]

    # Локальная библиотека (MMC-hint: local)
    if lib.get("MMC-hint") == "local":
        parts    = name.split(":")
        artifact = parts[1]
        version  = parts[2]
        clf      = parts[3] if len(parts) > 3 else None
        fname    = f"{artifact}-{version}" + (f"-{clf}" if clf else "") + ".jar"

        # 1) плоский путь (MultiMC кладёт local-либы без Maven-иерархии)
        p = os.path.join(local_libs_dir, fname)
        if os.path.isfile(p):
            return p

        # 2) Maven-иерархия внутри папки instance/libraries
        rel_loc = _maven_to_rel(name)
        p2 = os.path.join(local_libs_dir, rel_loc.replace("/", os.sep))
        if os.path.isfile(p2):
            return p2

        # 3) Уже скачана в глобальную libs_dir — используем её
        dest_global = os.path.join(libs_dir, rel_loc.replace("/", os.sep))
        if os.path.isfile(dest_global) and os.path.getsize(dest_global) > 0:
            return dest_global

        # 4) Fallback: скачать из Maven (на случай если jar не попала в zip)
        if on_status:
            on_status(f"Загрузка (local-fallback): {artifact}")
        dl_url = lib.get("downloads", {}).get("artifact", {}).get("url", "")
        if dl_url and _download(dl_url, dest_global):
            return dest_global
        if lib.get("url"):
            url = lib["url"].rstrip("/") + "/" + rel_loc
            if _download(url, dest_global):
                return dest_global
        for repo in MAVEN_REPOS:
            if _download(repo + rel_loc, dest_global):
                return dest_global

        log.error("Не найдена local-библиотека: %s", name)
        return None

    rel  = _maven_to_rel(name)
    dest = os.path.join(libs_dir, rel.replace("/", os.sep))

    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest

    # Поиск в кешах других лаунчеров (Prism, MultiMC и т.д.)
    for extra in _EXTRA_LIB_DIRS:
        if extra == libs_dir:
            continue
        p = os.path.join(extra, rel.replace("/", os.sep))
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            log.info("Найдена библиотека в стороннем кеше: %s", p)
            return p

    artifact_label = name.split(":")[1]

    # Явный URL из downloads
    dl_url = lib.get("downloads", {}).get("artifact", {}).get("url", "")
    if dl_url:
        if on_status:
            on_status(f"Загрузка: {artifact_label}")
        _download(dl_url, dest)
        return dest if os.path.isfile(dest) else None

    if on_status:
        on_status(f"Загрузка: {artifact_label}")

    # Кастомный Maven URL
    if lib.get("url"):
        url = lib["url"].rstrip("/") + "/" + rel
        if _download(url, dest):
            return dest

    # Перебор стандартных репозиториев
    for repo in MAVEN_REPOS:
        if _download(repo + rel, dest):
            return dest

    log.warning("Не удалось скачать библиотеку: %s", name)
    return None


def _extract_natives(lib: dict, libs_dir: str, natives_dir: str):
    """Распаковывает natives-jar в папку natives."""
    natives  = lib.get("natives", {})
    os_name  = _get_os()
    arch     = _get_arch()
    nat_key  = natives.get(os_name, "").replace("${arch}", arch)
    if not nat_key:
        return

    clfs = lib.get("downloads", {}).get("classifiers", {})
    dl   = clfs.get(nat_key, {})
    if not dl:
        return

    rel      = _maven_to_rel(f'{lib["name"]}:{nat_key}')
    jar_path = os.path.join(libs_dir, rel.replace("/", os.sep))

    if not os.path.isfile(jar_path):
        url = dl.get("url", "")
        if url:
            _download(url, jar_path)
    if not os.path.isfile(jar_path):
        return

    exclude    = lib.get("extract", {}).get("exclude", [])
    target_dir = pathlib.Path(natives_dir).resolve()
    os.makedirs(natives_dir, exist_ok=True)

    with zipfile.ZipFile(jar_path, "r") as zf:
        for entry in zf.namelist():
            # Защита от Zip Slip (path traversal)
            dest_path = (target_dir / entry).resolve()
            if not str(dest_path).startswith(str(target_dir)):
                log.warning("Zip Slip попытка пропущена: %s", entry)
                continue
            if any(entry.startswith(e) for e in exclude):
                continue
            zf.extract(entry, natives_dir)


# ══════════════════════════════════════════════════════════════════════════════
#  Установка (скачать все зависимости)
# ══════════════════════════════════════════════════════════════════════════════

def _libs_bundle_version_file(shared_dir: str) -> str:
    return os.path.join(shared_dir, ".gtnh_libs_version")


def _local_libs_version(shared_dir: str) -> str:
    try:
        return open(_libs_bundle_version_file(shared_dir)).read().strip()
    except Exception:
        return ""


def _try_install_libs_bundle(libs_url: str, shared_dir: str,
                              on_status=None, on_progress=None) -> bool:
    """
    Скачивает gtnh_libs.zip с сервера и распаковывает в shared_dir.
    Возвращает True если успешно, False при любой ошибке.
    Пропускает скачивание если версия уже совпадает.
    """
    try:
        # Проверяем версию на сервере
        ver_url = libs_url.rstrip("/").rsplit("/", 1)[0] + "/libs/version"
        # Пробуем /api/downloads/libs/version
        ver_url = libs_url + "/version" if not libs_url.endswith("/") else libs_url + "version"
        try:
            with urllib.request.urlopen(ver_url, timeout=5) as r:
                remote_ver = json.loads(r.read()).get("version", "")
        except Exception:
            remote_ver = ""

        local_ver = _local_libs_version(shared_dir)
        if remote_ver and remote_ver == local_ver:
            if on_status:
                on_status("Библиотеки актуальны (пропуск загрузки бандла)")
            return True

        if on_status:
            on_status("Загрузка пакета библиотек с сервера...")

        tmp_fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(tmp_fd)

        def _hook(b, bs, total):
            if on_progress and total > 0:
                on_progress(min(b * bs, total), total)

        urllib.request.urlretrieve(libs_url, tmp_zip, _hook)

        if on_status:
            on_status("Распаковка библиотек...")

        with zipfile.ZipFile(tmp_zip, "r") as zf:
            import pathlib
            target = pathlib.Path(shared_dir).resolve()
            for entry in zf.namelist():
                dest_path = (target / entry).resolve()
                if not str(dest_path).startswith(str(target)):
                    continue  # zip slip защита
                zf.extract(entry, shared_dir)

        os.unlink(tmp_zip)

        # Сохраняем версию
        if remote_ver:
            with open(_libs_bundle_version_file(shared_dir), "w") as f:
                f.write(remote_ver)

        if on_status:
            on_status("Пакет библиотек установлен!")
        return True

    except Exception as exc:
        log.warning("Не удалось установить бандл библиотек: %s", exc)
        return False


def install_dependencies(instance_dir: str, shared_dir: str,
                          on_status=None, on_progress=None,
                          libs_url: str = ""):
    """
    Скачивает MC jar, все библиотеки и asset-index.
    instance_dir — папка с mmc-pack.json / patches / .minecraft
    shared_dir   — папка для библиотек и ассетов (может совпадать с instance_dir)
    """
    # Найти реальную папку инстанса (zip мог распаковаться в подпапку)
    real = find_instance_dir(instance_dir)
    if real:
        instance_dir = real

    # Попытка скачать готовый бандл библиотек с сервера (экономит время)
    if libs_url:
        _try_install_libs_bundle(libs_url, shared_dir, on_status, on_progress)

    patches_dir  = os.path.join(instance_dir, "patches")
    local_libs   = os.path.join(instance_dir, "libraries")
    global_libs  = os.path.join(shared_dir,   "libraries")
    natives_dir  = os.path.join(shared_dir,   "natives")

    def status(msg):
        if on_status:
            on_status(msg)

    # ── Патчи (загружаем все, сортируем по "order" как MultiMC) ───────────
    mc_patch    = _load_json(os.path.join(patches_dir, "net.minecraft.json"))
    forge_patch = _load_json(os.path.join(patches_dir, "net.minecraftforge.json"))
    lwjgl3      = _load_json(os.path.join(patches_dir, "org.lwjgl3.json"))
    fp_patch    = _load_json(os.path.join(patches_dir, "me.eigenraven.lwjgl3ify.forgepatches.json"))
    la_patch    = _load_json(os.path.join(patches_dir, "me.eigenraven.lwjgl3ify.launchargs.json"))

    # Порядок патчей по полю "order" (аналогично MultiMC/Prism):
    #   net.minecraft       order -2
    #   org.lwjgl3          order -1
    #   forgepatches        order  3  ← должен быть ДО Forge!
    #   net.minecraftforge  order  5
    #   launchargs          order 100
    _ordered_patches = sorted(
        [mc_patch, lwjgl3, fp_patch, forge_patch, la_patch],
        key=lambda p: p.get("order", 0),
    )

    # ── Minecraft client.jar ───────────────────────────────────────────────
    mc_version   = mc_patch.get("version", "1.7.10")
    versions_dir = os.path.join(shared_dir, "versions", mc_version)
    os.makedirs(versions_dir, exist_ok=True)
    mc_jar = os.path.join(versions_dir, f"{mc_version}.jar")

    if not os.path.isfile(mc_jar):
        status(f"Загрузка Minecraft {mc_version} client.jar...")
        try:
            mc_jar_dl = mc_patch["mainJar"]["downloads"]["artifact"]
            _download(mc_jar_dl["url"], mc_jar, on_progress)
        except KeyError as e:
            raise RuntimeError(f"Не найден ключ в net.minecraft.json: {e}") from e

    # ── Библиотеки (в правильном порядке патчей) ──────────────────────────
    all_libs: list = []
    for patch in _ordered_patches:
        all_libs += patch.get("libraries", [])

    total = len(all_libs)
    for i, lib in enumerate(all_libs):
        if on_progress:
            on_progress(i + 1, total)
        if not _rule_ok(lib.get("rules", [])):
            continue
        if lib.get("natives"):
            _extract_natives(lib, global_libs, natives_dir)
        else:
            _resolve_lib(lib, global_libs, local_libs, status)

    # ── Asset index ────────────────────────────────────────────────────────
    asset_info = mc_patch.get("assetIndex", {})
    idx_dir    = os.path.join(shared_dir, "assets", "indexes")
    idx_file   = os.path.join(idx_dir, f'{asset_info.get("id", mc_version)}.json')
    os.makedirs(idx_dir, exist_ok=True)
    if asset_info and not os.path.isfile(idx_file):
        status("Загрузка asset index...")
        _download(asset_info["url"], idx_file)

    # ── Ассеты ────────────────────────────────────────────────────────────
    if os.path.isfile(idx_file):
        with open(idx_file, encoding="utf-8") as f:
            objects = json.load(f).get("objects", {})
        objs_dir = os.path.join(shared_dir, "assets", "objects")
        total_a  = len(objects)
        done_a   = 0
        for _name, info in objects.items():
            h      = info["hash"]
            prefix = h[:2]
            dest   = os.path.join(objs_dir, prefix, h)
            if not os.path.isfile(dest):
                url = f"https://resources.download.minecraft.net/{prefix}/{h}"
                _download(url, dest)
            done_a += 1
            if on_progress and done_a % 100 == 0:
                on_progress(done_a, total_a)
                status(f"Ассеты: {done_a}/{total_a}")

    status("Зависимости установлены!")


# ══════════════════════════════════════════════════════════════════════════════
#  Построение команды запуска
# ══════════════════════════════════════════════════════════════════════════════

def build_command(instance_dir: str, shared_dir: str,
                   username: str, memory_mb: int,
                   java_path: str = "java") -> list:
    """
    Строит полную java-команду для запуска GT:NH.
    Возвращает список строк (аргументы процесса).
    """
    real = find_instance_dir(instance_dir)
    if real:
        instance_dir = real

    patches_dir  = os.path.join(instance_dir, "patches")
    local_libs   = os.path.join(instance_dir, "libraries")
    global_libs  = os.path.join(shared_dir,   "libraries")
    natives_dir  = os.path.join(shared_dir,   "natives")
    mc_dir       = os.path.join(instance_dir, ".minecraft")
    assets_dir   = os.path.join(shared_dir,   "assets")

    mc_patch    = _load_json(os.path.join(patches_dir, "net.minecraft.json"))
    forge_patch = _load_json(os.path.join(patches_dir, "net.minecraftforge.json"))
    lwjgl3      = _load_json(os.path.join(patches_dir, "org.lwjgl3.json"))
    fp_patch    = _load_json(os.path.join(patches_dir, "me.eigenraven.lwjgl3ify.forgepatches.json"))
    la_patch    = _load_json(os.path.join(patches_dir, "me.eigenraven.lwjgl3ify.launchargs.json"))

    mc_version   = mc_patch.get("version", "1.7.10")
    versions_dir = os.path.join(shared_dir, "versions", mc_version)
    mc_jar       = os.path.join(versions_dir, f"{mc_version}.jar")

    # Порядок патчей по "order" — forgepatches (3) должен быть ДО Forge (5)
    _ordered_patches = sorted(
        [mc_patch, lwjgl3, fp_patch, forge_patch, la_patch],
        key=lambda p: p.get("order", 0),
    )

    # ── Classpath (порядок совпадает с MultiMC/Prism) ──────────────────────
    all_libs: list = []
    for patch in _ordered_patches:
        all_libs += patch.get("libraries", [])

    classpath = []
    for lib in all_libs:
        if not _rule_ok(lib.get("rules", [])):
            continue
        if lib.get("natives"):
            continue
        p = _resolve_lib(lib, global_libs, local_libs)
        if p and os.path.isfile(p):
            classpath.append(p)

    # MC jar в конец (Forge 1.7.10 требует это)
    if os.path.isfile(mc_jar):
        classpath.append(mc_jar)

    if not classpath:
        raise RuntimeError("Classpath пуст — библиотеки не найдены. Запустите установку.")

    # ── Guava 15.0 должен быть ПЕРВЫМ в classpath ─────────────────────────
    # AccessTransformer использует CharSource.readLines(LineProcessor), которого
    # нет в Guava 28+. Если новая Guava окажется раньше — AT сломается и
    # все поля останутся private → IllegalAccessError при загрузке модов.
    _guava15  = [p for p in classpath
                 if "guava" in os.path.basename(p).lower()
                 and "guava-15" in os.path.basename(p).lower()]
    _rest     = [p for p in classpath if p not in set(_guava15)]
    if _guava15:
        classpath = _guava15 + _rest
        log.info("Guava 15.0 поднята в начало classpath: %s", _guava15)

    # ── JVM args и tweakers из патчей (в порядке order) ──────────────────
    extra_jvm: list = []
    tweakers:  list = []
    for patch in _ordered_patches:
        extra_jvm += patch.get("+jvmArgs", [])
        tweakers  += patch.get("+tweakers", [])

    main_class = la_patch.get("mainClass",
                               "com.gtnewhorizons.retrofuturabootstrap.Main")
    asset_id   = mc_patch.get("assetIndex", {}).get("id", mc_version)

    # ── Сборка команды ────────────────────────────────────────────────────
    cmd = [java_path]

    # Память
    cmd += [f"-Xmx{memory_mb}M", "-Xms512M"]

    # G1GC
    cmd += [
        "-XX:+UseG1GC",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:G1NewSizePercent=20",
        "-XX:G1ReservePercent=20",
        "-XX:MaxGCPauseMillis=50",
    ]

    # LWJGL3ify --add-opens и -D флаги
    cmd += extra_jvm

    # Natives path
    cmd += [f"-Djava.library.path={natives_dir}"]

    # Classpath
    cmd += ["-cp", os.pathsep.join(classpath)]

    # Main class
    cmd += [main_class]

    # Аргументы игры
    cmd += [
        "--username",       username,
        "--version",        "GT_New_Horizons",
        "--gameDir",        mc_dir,
        "--assetsDir",      assets_dir,
        "--assetIndex",     asset_id,
        "--uuid",           "00000000000000000000000000000000",
        "--accessToken",    "0",
        "--userProperties", "{}",
        "--userType",       "legacy",
    ]

    # FML Tweaker
    for tweaker in tweakers:
        cmd += ["--tweakClass", tweaker]

    return cmd


def find_instance_dir(base_dir: str) -> "str | None":
    """
    Ищет папку MultiMC-инстанса (содержит mmc-pack.json).
    Проверяет base_dir и все папки первого уровня внутри него.
    """
    if os.path.isfile(os.path.join(base_dir, "mmc-pack.json")):
        return base_dir
    try:
        for entry in os.listdir(base_dir):
            sub = os.path.join(base_dir, entry)
            if os.path.isdir(sub) and os.path.isfile(
                    os.path.join(sub, "mmc-pack.json")):
                return sub
    except OSError:
        pass
    return None


def is_installed(base_dir: str, shared_dir: str) -> bool:
    """Проверяет, установлен ли клиент."""
    inst = find_instance_dir(base_dir)
    if inst is None:
        return False
    patches = os.path.join(inst, "patches", "net.minecraft.json")
    if not os.path.isfile(patches):
        return False
    try:
        mc_patch  = _load_json(patches)
        mc_ver    = mc_patch.get("version", "1.7.10")
        mc_jar    = os.path.join(shared_dir, "versions", mc_ver, f"{mc_ver}.jar")
    except Exception:
        mc_jar    = os.path.join(shared_dir, "versions", "1.7.10", "1.7.10.jar")
    return os.path.isfile(mc_jar)
