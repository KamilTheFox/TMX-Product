import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import asyncpg
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_BASE = os.getenv("API_BASE", "http://109.254.86.44:8000")

ADMIN_HTML = BASE_DIR / "admin.html"
PHOTO_DIR = Path(os.getenv("PHOTO_DIR", "/home/user/tmx/foto"))
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif",
}

# --- строка подключения к БД -------------------------------------------------
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
    or os.getenv("DB_URL")
    or os.getenv("POSTGRES_DSN")
)
if DATABASE_URL:
    DATABASE_URL = (
        DATABASE_URL
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )

DB_CONFIG = None
if not DATABASE_URL:
    DB_CONFIG = {
        "host": os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or "127.0.0.1",
        "port": int(os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or 5432),
        "user": os.getenv("DB_USER") or os.getenv("POSTGRES_USER"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        "database": os.getenv("DB_NAME") or os.getenv("POSTGRES_DB"),
    }


# ---------------------------------------------------------------------------
# Связь «файл ↔ дефект»: имя файла = id дефекта
# ---------------------------------------------------------------------------
def defect_id_from_name(name_or_path: str) -> Optional[int]:
    stem = Path(name_or_path).stem
    try:
        return int(stem)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Жизненный цикл
# ---------------------------------------------------------------------------
async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS ban BOOLEAN DEFAULT false"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if DATABASE_URL:
        pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
    else:
        if not (DB_CONFIG["user"] and DB_CONFIG["password"] and DB_CONFIG["database"]):
            raise RuntimeError(
                "Не найдены настройки БД в .env. "
                "Нужен DATABASE_URL или DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME"
            )
        pool = await asyncpg.create_pool(min_size=1, max_size=5, **DB_CONFIG)

    app.state.db_pool = pool
    app.state.http = httpx.AsyncClient(timeout=30.0)

    await ensure_schema(pool)
    print(f"DB connected | PHOTO_DIR = {PHOTO_DIR}")

    try:
        yield
    finally:
        await app.state.http.aclose()
        await pool.close()


app = FastAPI(title="TMX Admin Panel", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/photos", StaticFiles(directory=str(PHOTO_DIR)), name="photos")


def get_pool() -> asyncpg.Pool:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=500, detail="DB pool is not initialized")
    return pool


# ---------------------------------------------------------------------------
# Данные дефектов для карточек фотографий
# Линия и поезд определяются через вагон: вагон → поезд → линия
# ---------------------------------------------------------------------------
async def get_defects_map() -> dict:
    """
    { id_дефекта: {data_time, line_label, train, wagon, wagon_id, category,
                   text_prob, id_user} }
    Связь: defects.id_wagon → wagons → trains → lines
    """
    try:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch("""
                SELECT d.id,
                       to_char(d.data_time, 'YYYY-MM-DD HH24:MI:SS') AS data_time,
                       d.id_wagon,
                       d.id_user,
                       w.number::text  AS wagon_number,
                       t.number::text  AS train_number,
                       l.number::text  AS line_number,
                       l.name::text    AS line_name,
                       c.name::text    AS category,
                       tp.text::text   AS text_prob
                FROM defects d
                LEFT JOIN wagons    w  ON w.id_wagon = d.id_wagon
                LEFT JOIN trains    t  ON t.id_train = w.id_train
                LEFT JOIN lines     l  ON l.id_line  = t.id_line
                LEFT JOIN category  c  ON c.id_categ = d.id_categ
                LEFT JOIN text_prob tp ON tp.id_text = d.id_text
            """)
    except Exception as e:
        print("GET DEFECTS MAP ERROR:", e)
        return {}

    result = {}
    for r in rows:
        if r["line_number"] and r["line_name"]:
            line_label = f'Линия {r["line_number"]} - {r["line_name"]}'
        else:
            line_label = "-"
        result[r["id"]] = {
            "data_time":   r["data_time"],
            "line_label":  line_label,
            "train":       r["train_number"] or "-",
            "wagon":       r["wagon_number"] or "-",
            "wagon_id":    r["id_wagon"],
            "category":    r["category"] or "",
            "text_prob":   r["text_prob"] or "",
            "id_user":     r["id_user"],
        }
    return result


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------
class UserUpdate(BaseModel):
    admin: Optional[bool] = None
    ban: Optional[bool] = None


# ---------------------------------------------------------------------------
# Панель
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    if not ADMIN_HTML.exists():
        raise HTTPException(status_code=500, detail="admin.html не найден рядом с main_new.py")
    return FileResponse(ADMIN_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# Фотографии
# ---------------------------------------------------------------------------
@app.get("/admin-api/photos")
async def get_photos():
    defects_by_id = await get_defects_map()
    photos = []

    if PHOTO_DIR.exists():
        for img in PHOTO_DIR.rglob("*"):
            if not (img.is_file() and img.suffix.lower() in ALLOWED_PHOTO_EXTENSIONS):
                continue

            rel_path = img.relative_to(PHOTO_DIR).as_posix()
            stat = img.stat()
            file_dt = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            defect_id = defect_id_from_name(img.name)
            defect = defects_by_id.get(defect_id) if defect_id is not None else None

            if defect:
                photos.append({
                    "name":       img.name,
                    "rel_path":   rel_path,
                    "path":       f"/photos/{rel_path}",
                    "size":       stat.st_size,
                    "modified":   stat.st_mtime,
                    "defect_id":  defect_id,
                    "line_label": defect["line_label"],
                    "train":      defect["train"],
                    "wagon":      defect["wagon"],
                    "wagon_id":   defect["wagon_id"],
                    "category":   defect["category"],
                    "text_prob":  defect["text_prob"],
                    "uploaded_at": defect["data_time"],
                    "id_user":    defect["id_user"],
                })
            else:
                # Файл без записи в defects — показываем как есть
                photos.append({
                    "name":       img.name,
                    "rel_path":   rel_path,
                    "path":       f"/photos/{rel_path}",
                    "size":       stat.st_size,
                    "modified":   stat.st_mtime,
                    "defect_id":  None,
                    "line_label": "-",
                    "train":      "-",
                    "wagon":      "-",
                    "wagon_id":   None,
                    "category":   "",
                    "text_prob":  "",
                    "uploaded_at": file_dt,
                    "id_user":    None,
                })

    photos.sort(key=lambda x: (x["uploaded_at"] or "", x["modified"]), reverse=True)
    return {"photos": photos}


@app.delete("/admin-api/photos/{photo_path:path}")
async def delete_photo(photo_path: str):
    base = PHOTO_DIR.resolve()
    file_path = (PHOTO_DIR / photo_path).resolve()

    try:
        file_path.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad path")

    if not file_path.exists() or not file_path.is_file():
        return {"ok": False, "message": "Photo not found"}

    defect_id = defect_id_from_name(file_path.name)
    defect_deleted = False
    if defect_id is not None:
        try:
            async with get_pool().acquire() as conn:
                row = await conn.fetchrow(
                    "DELETE FROM defects WHERE id = $1 RETURNING id", defect_id
                )
                defect_deleted = row is not None
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Не удалось удалить запись дефекта: {e}")

    file_path.unlink()

    parent = file_path.parent
    if parent != base and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    return {
        "ok": True,
        "message": "Photo deleted",
        "defect_id": defect_id,
        "defect_deleted": defect_deleted,
    }


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------
@app.get("/api/admin/users")
async def get_admin_users():
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT id_user, email::text AS email, coin, admin,
                   COALESCE(ban, false) AS ban, created_at
            FROM users
            ORDER BY id_user DESC
        """)
    return [
        {**dict(row), "created_at": row["created_at"].isoformat() if row["created_at"] else None}
        for row in rows
    ]


@app.patch("/api/admin/users/{user_id}")
async def update_admin_user(user_id: int, data: UserUpdate):
    if data.admin is None and data.ban is None:
        raise HTTPException(status_code=400, detail="Нечего обновлять")

    sets, params = [], []
    if data.admin is not None:
        params.append(data.admin)
        sets.append(f"admin = ${len(params)}")
    if data.ban is not None:
        params.append(data.ban)
        sets.append(f"ban = ${len(params)}")
    params.append(user_id)

    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE users SET {', '.join(sets)} WHERE id_user = ${len(params)} "
            "RETURNING id_user, email::text AS email, admin, COALESCE(ban, false) AS ban",
            *params,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return dict(row)


# ---------------------------------------------------------------------------
# Прокси на внешний API
# ---------------------------------------------------------------------------
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_to_api(request: Request, path: str):
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    client: httpx.AsyncClient = request.app.state.http

    try:
        resp = await client.request(
            method=request.method,
            url=f"{API_BASE}/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})

    if "application/json" in resp.headers.get("content-type", ""):
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(status_code=resp.status_code, content={"status": resp.status_code})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)