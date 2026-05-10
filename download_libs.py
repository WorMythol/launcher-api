"""
Скачивает все библиотеки GT:NH в папку output_libs/.
Запусти один раз, потом запакуй output_libs/ в zip и залей на сервер.

Использование:
    python download_libs.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
import zipfile
import pathlib

# ── Настройки ─────────────────────────────────────────────────────────────────

# Путь к инстансу GT:NH (папка с mmc-pack.json)
INSTANCE_DIR = r"C:\Users\bulga\AppData\Roaming\.minecraft\GT New Horizons 2.8.4"

# Куда складывать скачанные библиотеки
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output_libs")

# Дополнительные папки для поиска уже существующих jar (Prism и т.д.)
EXTRA_SEARCH = [
    r"C:\Users\bulga\AppData\Roaming\PrismLauncher\libraries",
    r"C:\Users\bulga\AppData\Roaming\.minecraft\libraries",
    r"C:\Users\bulga\AppData\Roaming\MultiMC\libraries",
]

MAVEN_REPOS = [
    "https://maven.minecraftforge.net/",
    "https://libraries.minecraft.net/",
    "https://files.prismlauncher.org/maven/",
]

# ── Утилиты ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def maven_rel(name):
    parts    = name.split(":")
    group    = parts[0].replace(".", "/")
    artifact = parts[1]
    version  = parts[2]
    clf      = parts[3] if len(parts) > 3 else None
    fname    = f"{artifact}-{version}" + (f"-{clf}" if clf else "") + ".jar"
    return f"{group}/{artifact}/{version}/{fname}"


def download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    try:
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
        return True
    except Exception as e:
        print(f"    FAIL {url}  →  {e}")
        for p in (tmp, dest):
            try: os.unlink(p)
            except: pass
        return False


def find_existing(rel):
    """Ищет jar в кешах других лаунчеров."""
    for base in EXTRA_SEARCH:
        p = os.path.join(base, rel.replace("/", os.sep))
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def get_os():
    import platform
    s = platform.system().lower()
    return "windows" if s == "windows" else ("osx" if s == "darwin" else "linux")


def rule_ok(rules):
    if not rules:
        return True
    os_name = get_os()
    result  = False
    for r in rules:
        action  = r["action"]
        os_rule = r.get("os", {})
        match   = (not os_rule) or os_rule.get("name", "") == os_name
        if action == "allow" and match:
            result = True
        elif action == "disallow" and match:
            result = False
    return result

# ── Основная логика ───────────────────────────────────────────────────────────

def main():
    patches_dir = os.path.join(INSTANCE_DIR, "patches")
    local_libs  = os.path.join(INSTANCE_DIR, "libraries")

    print(f"Инстанс:   {INSTANCE_DIR}")
    print(f"Вывод:     {OUTPUT_DIR}")
    print()

    # Патчи
    patch_files = [
        "net.minecraft.json",
        "net.minecraftforge.json",
        "org.lwjgl3.json",
        "me.eigenraven.lwjgl3ify.forgepatches.json",
        "me.eigenraven.lwjgl3ify.launchargs.json",
    ]
    patches = {}
    for pf in patch_files:
        path = os.path.join(patches_dir, pf)
        if os.path.isfile(path):
            patches[pf] = load_json(path)
            print(f"  OK {pf}")
        else:
            print(f"  FAIL {pf} — не найден, пропускаем")
    print()

    # Minecraft client.jar
    mc_patch = patches.get("net.minecraft.json", {})
    mc_ver   = mc_patch.get("version", "1.7.10")
    mc_dest  = os.path.join(OUTPUT_DIR, "versions", mc_ver, f"{mc_ver}.jar")

    if not os.path.isfile(mc_dest):
        try:
            mc_url = mc_patch["mainJar"]["downloads"]["artifact"]["url"]
            print(f"Скачиваю Minecraft {mc_ver}.jar ...")
            if download(mc_url, mc_dest):
                print(f"  OK {mc_ver}.jar  ({os.path.getsize(mc_dest) // 1024} КБ)")
            else:
                print(f"  FAIL Не удалось скачать {mc_ver}.jar")
        except KeyError:
            print("  ! mainJar не найден в net.minecraft.json")
    else:
        print(f"  OK {mc_ver}.jar уже есть")
    print()

    # Библиотеки
    all_libs = []
    for pf in patch_files:
        all_libs += patches.get(pf, {}).get("libraries", [])

    print(f"Библиотек в патчах: {len(all_libs)}")
    print()

    ok = 0
    skip = 0
    fail = 0

    for lib in all_libs:
        if not rule_ok(lib.get("rules", [])):
            skip += 1
            continue
        if lib.get("natives"):
            skip += 1
            continue

        name = lib["name"]
        artifact = name.split(":")[1]

        # Локальная библиотека из инстанса
        if lib.get("MMC-hint") == "local":
            parts   = name.split(":")
            art     = parts[1]
            ver     = parts[2]
            clf     = parts[3] if len(parts) > 3 else None
            fname   = f"{art}-{ver}" + (f"-{clf}" if clf else "") + ".jar"
            src     = os.path.join(local_libs, fname)
            if os.path.isfile(src):
                rel  = maven_rel(name)
                dest = os.path.join(OUTPUT_DIR, "libraries", rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                import shutil
                shutil.copy2(src, dest)
                print(f"  OK {artifact}  (local, скопирован)")
                ok += 1
                continue

        rel  = maven_rel(name)
        dest = os.path.join(OUTPUT_DIR, "libraries", rel.replace("/", os.sep))

        # Уже скачан
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            ok += 1
            continue

        # Ищем в кешах других лаунчеров
        existing = find_existing(rel)
        if existing:
            import shutil
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(existing, dest)
            print(f"  OK {artifact}  (из кеша лаунчера)")
            ok += 1
            continue

        # Скачиваем
        print(f"  DL {artifact} ...")
        dl_url = lib.get("downloads", {}).get("artifact", {}).get("url", "")
        urls   = []
        if dl_url:
            urls.append(dl_url)
        if lib.get("url"):
            urls.append(lib["url"].rstrip("/") + "/" + rel)
        for repo in MAVEN_REPOS:
            urls.append(repo + rel)

        downloaded = False
        for url in urls:
            if download(url, dest):
                size = os.path.getsize(dest) // 1024
                print(f"    OK {artifact}  ({size} КБ)")
                downloaded = True
                ok += 1
                break
        if not downloaded:
            print(f"    FAIL НЕ УДАЛОСЬ: {name}")
            fail += 1

    print()
    print(f"Готово: {ok} ОК, {skip} пропущено, {fail} ошибок")
    print(f"Результат в: {OUTPUT_DIR}")
    print()
    print("Следующий шаг:")
    print(f'  7z a gtnh_libs.zip "{OUTPUT_DIR}\\*" -mx=5')
    print("  Залей gtnh_libs.zip на сервер в /api/downloads/libs")


if __name__ == "__main__":
    main()
