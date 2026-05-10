"""
API-сервер лаунчера GT:NH.

Запуск:
    pip install fastapi uvicorn psycopg2-binary bcrypt
    python server.py

Или через uvicorn напрямую:
    uvicorn server:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import bcrypt
import psycopg2
import psycopg2.extras
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Конфигурация ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_HERE = os.path.dirname(__file__)

def _load_brand() -> dict:
    path = os.path.join(_HERE, "brand.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

BRAND          = _load_brand()
DB_URL         = BRAND.get("db_url", "")
CLIENT_VERSION = BRAND.get("fixed_version", "1.0.0")
DOWNLOAD_URL   = BRAND.get("client_download_url", "")

# Папка со статическими файлами (gtnh_libs.zip и др.)
STATIC_DIR = os.path.join(_HERE, "static")

# Время жизни токена сессии (секунды)
TOKEN_TTL = 60 * 60 * 24 * 30   # 30 дней


# ── Подключение к БД ──────────────────────────────────────────────────────────

def get_conn():
    """Создаёт новое соединение с PostgreSQL."""
    return psycopg2.connect(DB_URL, connect_timeout=10)


def init_db():
    """Создаёт таблицы, если их ещё нет."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Таблица пользователей (если не существует)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        login         TEXT PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        status        TEXT NOT NULL DEFAULT 'active'
                    )
                """)
                # Таблица сессий
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        token      TEXT PRIMARY KEY,
                        login      TEXT NOT NULL,
                        created_at BIGINT NOT NULL,
                        expires_at BIGINT NOT NULL
                    )
                """)
                # Таблица новостей
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS news (
                        id         SERIAL PRIMARY KEY,
                        title      TEXT NOT NULL,
                        body       TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                """)
        log.info("БД инициализирована")
    finally:
        conn.close()


# ── Утилиты сессий ────────────────────────────────────────────────────────────

def create_session(login: str) -> str:
    """Создаёт токен сессии, сохраняет в БД, возвращает токен."""
    token      = secrets.token_hex(32)
    now        = int(time.time())
    expires_at = now + TOKEN_TTL
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (token, login, created_at, expires_at)"
                    " VALUES (%s, %s, %s, %s)",
                    (token, login, now, expires_at),
                )
    finally:
        conn.close()
    return token


def verify_token(token: str) -> "str | None":
    """Проверяет токен, возвращает логин или None если невалиден/истёк."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT login, expires_at FROM sessions WHERE token = %s",
                (token,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    login, expires_at = row
    if int(time.time()) > expires_at:
        delete_session(token)
        return None
    return login


def delete_session(token: str):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
    finally:
        conn.close()


def cleanup_expired_sessions():
    """Удаляет истёкшие сессии."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE expires_at < %s",
                    (int(time.time()),),
                )
    finally:
        conn.close()


# ── Приложение ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_expired_sessions()
    yield

app = FastAPI(title="Launcher API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Зависимость: проверка Bearer-токена ──────────────────────────────────────

def require_auth(authorization: str = Header(default="")) -> str:
    """Извлекает и проверяет Bearer токен. Возвращает логин."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    token = authorization.removeprefix("Bearer ").strip()
    login = verify_token(token)
    if login is None:
        raise HTTPException(status_code=401, detail="Токен недействителен или истёк")
    return login


# ══════════════════════════════════════════════════════════════════════════════
#  Эндпоинты
# ══════════════════════════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    login:    str
    password: str


@app.post("/api/login")
def api_login(body: LoginRequest):
    """
    Авторизация по логину и паролю.
    Возвращает токен сессии при успехе.
    """
    login    = body.login.strip()
    password = body.password

    if not login or not password:
        raise HTTPException(status_code=400, detail="Пустой логин или пароль")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT login, password_hash, status FROM users WHERE login = %s",
                (login,),
            )
            row = cur.fetchone()
    except Exception as e:
        log.exception("Ошибка БД при входе")
        raise HTTPException(status_code=503, detail="Ошибка базы данных") from e
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    db_login, pw_hash, status = row

    if status == "banned":
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")

    if not bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    token = create_session(db_login)
    log.info("Вход: %s", db_login)
    return {"success": True, "login": db_login, "token": token}


@app.get("/api/me")
def api_me(login: str = Depends(require_auth)):
    """Проверяет токен и возвращает данные текущего пользователя."""
    return {"login": login}


@app.get("/api/news")
def api_news():
    """Возвращает последние новости."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT title, body FROM news ORDER BY created_at DESC LIMIT 10"
            )
            rows = cur.fetchall()
    except Exception as e:
        log.warning("Ошибка при получении новостей: %s", e)
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/api/client/version")
def api_client_version():
    """Возвращает актуальную версию клиента."""
    return {"version": CLIENT_VERSION}


@app.get("/api/downloads/client/latest")
def api_client_download():
    """Редирект на актуальный zip клиента."""
    from fastapi.responses import RedirectResponse
    if not DOWNLOAD_URL:
        raise HTTPException(status_code=404, detail="URL скачивания не настроен")
    return RedirectResponse(url=DOWNLOAD_URL)


@app.get("/api/downloads/libs")
def api_libs_download():
    """
    Отдаёт gtnh_libs.zip — предварительно скачанный набор библиотек для GT:NH.
    Положи файл в server/static/gtnh_libs.zip.
    """
    from fastapi.responses import FileResponse
    path = os.path.join(STATIC_DIR, "gtnh_libs.zip")
    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="gtnh_libs.zip не найден. Положи его в server/static/gtnh_libs.zip"
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename="gtnh_libs.zip",
    )


@app.get("/api/downloads/libs/version")
def api_libs_version():
    """
    Возвращает версию (дату изменения) gtnh_libs.zip.
    Лаунчер сравнивает её, чтобы не качать повторно.
    """
    path = os.path.join(STATIC_DIR, "gtnh_libs.zip")
    if not os.path.isfile(path):
        return {"version": "0"}
    mtime = int(os.path.getmtime(path))
    size  = os.path.getsize(path)
    return {"version": f"{mtime}-{size}"}


# ── Точка входа ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
