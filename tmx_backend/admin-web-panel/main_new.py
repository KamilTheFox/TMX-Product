import os
import httpx
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import asyncpg
from datetime import datetime
import asyncio
import json


app = FastAPI(title="Admin Panel")

# Разрешаем CORS для всего
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_BASE = "http://109.254.86.44:8000"

load_dotenv(Path(__file__).with_name(".env"))

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

# Вот этой переменной у тебя не было
db_pool = None


@app.on_event("startup")
async def startup_db():
    global db_pool

    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=5
        )
    else:
        if (
            not DB_CONFIG["user"]
            or not DB_CONFIG["password"]
            or not DB_CONFIG["database"]
        ):
            raise RuntimeError(
                "Не найдены настройки БД в .env. "
                "Нужен DATABASE_URL или DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME"
            )

        db_pool = await asyncpg.create_pool(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            min_size=1,
            max_size=5
        )

    print("DB connected")


@app.on_event("shutdown")
async def shutdown_db():
    global db_pool

    if db_pool is not None:
        await db_pool.close()
        db_pool = None

# Директория для фото
PHOTO_DIR = Path("/home/user/tmx/foto")
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

PHOTO_META_FILE = PHOTO_DIR / "photos_meta.json"


def load_photo_meta() -> dict:
    if not PHOTO_META_FILE.exists():
        return {}

    try:
        return json.loads(PHOTO_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_photo_meta(meta: dict):
    tmp_file = PHOTO_META_FILE.with_suffix(".tmp")
    tmp_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp_file.replace(PHOTO_META_FILE)


async def get_lines_map() -> dict:
    """
    Возвращает словарь:
    {
        1: "Линия 1 - Сокольническая линия",
        ...
    }
    """
    try:
        pool = await get_db()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id_line, number::text AS number, name::text AS name
                FROM lines
                ORDER BY id_line
            """)

        result = {}

        for row in rows:
            result[row["id_line"]] = f'Линия {row["number"]} - {row["name"]}'

        return result

    except Exception as e:
        print("GET LINES MAP ERROR:", e)
        return {}

ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
upload_lock = asyncio.Lock()


def get_next_photo_number() -> int:
    max_number = 0

    for file_path in PHOTO_DIR.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in ALLOWED_PHOTO_EXTENSIONS:
            continue

        try:
            number = int(file_path.stem)
            max_number = max(max_number, number)
        except ValueError:
            continue

    return max_number + 1

app.mount("/photos", StaticFiles(directory=str(PHOTO_DIR)), name="photos")
print("PHOTO_DIR =", PHOTO_DIR)
print("EXISTS =", PHOTO_DIR.exists())

for x in PHOTO_DIR.rglob("*"):
    print("FOUND:", x)
# Прокси для всех API запросов

class TrainCreate(BaseModel):
    number: str
    line_id: Optional[int] = None


class TrainLineUpdate(BaseModel):
    new_id_line: Optional[int] = None


class WagonCreate(BaseModel):
    number: str
    train_id: Optional[int] = None


class WagonTrainUpdate(BaseModel):
    new_id_train: Optional[int] = None

class CategoryCreate(BaseModel):
    name: str


class CategoryUpdate(BaseModel):
    name: str


class UserUpdate(BaseModel):
    admin: Optional[bool] = None
    banned: Optional[bool] = None


async def get_db():
    global db_pool

    if db_pool is None:
        raise HTTPException(
            status_code=500,
            detail="DB pool is not initialized"
        )

    return db_pool

@app.get("/debug-path")
async def debug_path():

    file_path = PHOTO_DIR / "2026-05-05" / "1.png"

    return {
        "exists": file_path.exists(),
        "absolute": str(file_path),
        "is_file": file_path.is_file()
    }


# Загрузка фото
@app.post("/upload-photo")
async def upload_photo(
    foto: UploadFile = File(...),
    line: Optional[int] = Form(None),
    wagon: str = Form(""),
    train: str = Form(""),
    text_prob: str = Form(""),
    authorization: str = Header(None)
):
    original_name = foto.filename or ""
    ext = Path(original_name).suffix.lower()

    if ext == ".jpeg":
        ext = ".jpg"

    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Можно загружать только jpg, jpeg, png, webp или gif"
        )

    content = await foto.read()

    if not content:
        raise HTTPException(status_code=400, detail="Файл пустой")

    max_size = 15 * 1024 * 1024

    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail="Файл слишком большой. Максимум 15 MB"
        )

    date_folder = datetime.now().strftime("%Y-%m-%d")
    save_dir = PHOTO_DIR / date_folder
    save_dir.mkdir(parents=True, exist_ok=True)

    uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with upload_lock:
        photo_number = get_next_photo_number()
        filename = f"{photo_number}{ext}"
        file_path = save_dir / filename

        file_path.write_bytes(content)

        rel_path = file_path.relative_to(PHOTO_DIR).as_posix()

        meta = load_photo_meta()
        meta[rel_path] = {
            "line": line,
            "wagon": wagon.strip() if wagon else "",
            "train": train.strip() if train else "",
            "text_prob": text_prob.strip() if text_prob else "",
            "uploaded_at": uploaded_at,
            "original_name": original_name
        }
        save_photo_meta(meta)

        print(
            "UPLOADED PHOTO:",
            file_path,
            "| line =", line,
            "| wagon =", wagon,
            "| train =", train,
            "| text_prob =", text_prob,
            "| uploaded_at =", uploaded_at
        )

    rel_path = file_path.relative_to(PHOTO_DIR).as_posix()

    return {
        "ok": True,
        "message": "Фото загружено",
        "filename": filename,
        "date_folder": date_folder,
        "path": f"/photos/{rel_path}",
        "absolute_path": str(file_path),
        "line": line,
        "wagon": wagon,
        "train": train,
        "text_prob": text_prob,
        "uploaded_at": uploaded_at
    }
# Список фото из локальной директории
@app.get("/admin-api/photos")
async def get_photos():
    photos = []
    meta = load_photo_meta()
    lines_map = await get_lines_map()

    if PHOTO_DIR.exists():
        for img in PHOTO_DIR.rglob("*"):
            if img.is_file() and img.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                rel_path = img.relative_to(PHOTO_DIR).as_posix()
                item_meta = meta.get(rel_path, {})

                line_id = item_meta.get("line")
                line_label = "-"

                if line_id is not None and line_id != "":
                    try:
                        line_label = lines_map.get(int(line_id), f"ID {line_id}")
                    except Exception:
                        line_label = f"ID {line_id}"

                uploaded_at = item_meta.get("uploaded_at")

                if not uploaded_at:
                    uploaded_at = datetime.fromtimestamp(
                        img.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M:%S")

                photos.append({
                    "name": img.name,
                    "rel_path": rel_path,
                    "path": f"/photos/{rel_path}",
                    "size": img.stat().st_size,
                    "modified": img.stat().st_mtime,
                    "date_folder": img.parent.name,

                    "line": line_id,
                    "line_label": line_label,
                    "wagon": item_meta.get("wagon") or "-",
                    "train": item_meta.get("train") or "-",
                    "text_prob": item_meta.get("text_prob") or "",
                    "uploaded_at": uploaded_at
                })

    return {
        "photos": sorted(photos, key=lambda x: x["modified"], reverse=True)
    }

# Удаление фото
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
    rel_path = file_path.relative_to(base).as_posix()
    file_path.unlink()
    meta = load_photo_meta()

    if rel_path in meta:
        del meta[rel_path]
        save_photo_meta(meta)
    parent = file_path.parent
    if parent != base and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    return {"ok": True, "message": "Photo deleted"}

# ========== ПОЕЗДА ==========

@app.get("/api/admin/trains")
async def get_admin_trains():
    pool = await get_db()

    query = """
        SELECT
            t.id_train,
            t.number::text AS number,
            t.id_line,
            l.name::text AS line_name,
            l.number::text AS line_number
        FROM trains t
        LEFT JOIN lines l ON l.id_line = t.id_line
        ORDER BY t.id_train DESC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query)

    result = []
    for row in rows:
        item = dict(row)

        if item["line_number"] and item["line_name"]:
            item["line_name"] = f'Линия {item["line_number"]} - {item["line_name"]}'
        else:
            item["line_name"] = None

        result.append(item)

    return result


@app.post("/api/admin/trains")
async def create_admin_train(data: TrainCreate):
    pool = await get_db()

    number = data.number.strip() if data.number else ""

    if not number:
        raise HTTPException(status_code=400, detail="Train number is required")

    if len(number) > 6:
        raise HTTPException(status_code=400, detail="Номер поезда должен быть не длиннее 6 символов")

    async with pool.acquire() as conn:
        if data.line_id is not None:
            line_exists = await conn.fetchval(
                "SELECT 1 FROM lines WHERE id_line = $1",
                data.line_id
            )

            if not line_exists:
                raise HTTPException(status_code=400, detail="Line not found")

        try:
            row = await conn.fetchrow(
                """
                INSERT INTO trains (number, id_line)
                VALUES ($1, $2)
                RETURNING id_train, number::text AS number, id_line
                """,
                number,
                data.line_id
            )
        except asyncpg.CheckViolationError:
            raise HTTPException(
                status_code=400,
                detail="Номер поезда может содержать только цифры, буквы, _ или -, максимум 6 символов"
            )

    return dict(row)


@app.patch("/api/admin/trains/{train_id}/line")
async def update_admin_train_line(train_id: int, data: TrainLineUpdate):
    pool = await get_db()

    async with pool.acquire() as conn:
        if data.new_id_line is not None:
            line_exists = await conn.fetchval(
                "SELECT 1 FROM lines WHERE id_line = $1",
                data.new_id_line
            )

            if not line_exists:
                raise HTTPException(status_code=400, detail="Line not found")

        row = await conn.fetchrow(
            """
            UPDATE trains
            SET id_line = $1
            WHERE id_train = $2
            RETURNING id_train, number::text AS number, id_line
            """,
            data.new_id_line,
            train_id
        )

    if not row:
        raise HTTPException(status_code=404, detail="Train not found")

    return dict(row)


@app.delete("/api/admin/trains/{train_id}")
async def delete_admin_train(train_id: int):
    pool = await get_db()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Сначала отвязываем вагоны от поезда
            await conn.execute(
                """
                UPDATE wagons
                SET id_train = NULL
                WHERE id_train = $1
                """,
                train_id
            )

            try:
                row = await conn.fetchrow(
                    """
                    DELETE FROM trains
                    WHERE id_train = $1
                    RETURNING id_train
                    """,
                    train_id
                )
            except asyncpg.ForeignKeyViolationError:
                raise HTTPException(
                    status_code=409,
                    detail="Нельзя удалить поезд: он используется в дефектах"
                )

    if not row:
        raise HTTPException(status_code=404, detail="Train not found")

    return {"ok": True, "message": "Train deleted"}


# ========== ВАГОНЫ ==========

@app.get("/api/admin/wagons")
async def get_admin_wagons():
    pool = await get_db()

    query = """
        SELECT
            w.id_wagon,
            w.number::text AS number,
            w.id_train,
            t.number::text AS train_number
        FROM wagons w
        LEFT JOIN trains t ON t.id_train = w.id_train
        ORDER BY w.id_wagon DESC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query)

    return [dict(row) for row in rows]


@app.post("/api/admin/wagons")
async def create_admin_wagon(data: WagonCreate):
    pool = await get_db()

    number = data.number.strip() if data.number else ""

    if not number:
        raise HTTPException(status_code=400, detail="Wagon number is required")

    if len(number) > 6:
        raise HTTPException(status_code=400, detail="Номер вагона должен быть не длиннее 6 символов")

    async with pool.acquire() as conn:
        if data.train_id is not None:
            train_exists = await conn.fetchval(
                "SELECT 1 FROM trains WHERE id_train = $1",
                data.train_id
            )

            if not train_exists:
                raise HTTPException(status_code=400, detail="Train not found")

        try:
            row = await conn.fetchrow(
                """
                INSERT INTO wagons (number, id_train)
                VALUES ($1, $2)
                RETURNING id_wagon, number::text AS number, id_train
                """,
                number,
                data.train_id
            )
        except asyncpg.CheckViolationError:
            raise HTTPException(
                status_code=400,
                detail="Номер вагона может содержать только цифры, буквы, _ или -, максимум 6 символов"
            )

    return dict(row)


@app.patch("/api/admin/wagons/{wagon_id}/train")
async def update_admin_wagon_train(wagon_id: int, data: WagonTrainUpdate):
    pool = await get_db()

    async with pool.acquire() as conn:
        if data.new_id_train is not None:
            train_exists = await conn.fetchval(
                "SELECT 1 FROM trains WHERE id_train = $1",
                data.new_id_train
            )

            if not train_exists:
                raise HTTPException(status_code=400, detail="Train not found")

        row = await conn.fetchrow(
            """
            UPDATE wagons
            SET id_train = $1
            WHERE id_wagon = $2
            RETURNING id_wagon, number::text AS number, id_train
            """,
            data.new_id_train,
            wagon_id
        )

    if not row:
        raise HTTPException(status_code=404, detail="Wagon not found")

    return dict(row)


@app.delete("/api/admin/wagons/{wagon_id}")
async def delete_admin_wagon(wagon_id: int):
    pool = await get_db()

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                DELETE FROM wagons
                WHERE id_wagon = $1
                RETURNING id_wagon
                """,
                wagon_id
            )
        except asyncpg.ForeignKeyViolationError:
            raise HTTPException(
                status_code=409,
                detail="Нельзя удалить вагон: он используется в дефектах"
            )

    if not row:
        raise HTTPException(status_code=404, detail="Wagon not found")

    return {"ok": True, "message": "Wagon deleted"}

# Админ-панель
@app.get("/")
async def root():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Metro Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-100">
    <div class="container mx-auto p-4">
        <div class="bg-white rounded-lg shadow p-6">
            <h1 class="text-2xl font-bold mb-4 text-center"> Metro Defects Admin Panel</h1>
            
            <!-- Панель входа -->
            <div id="loginPanel">
                <div class="max-w-md mx-auto">
                    <h2 class="text-xl mb-4 text-center">Вход в систему</h2>
                    <div class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium mb-1">Email:</label>
                            <input type="email" id="email" placeholder="ta3at@mail.ru" class="w-full border p-2 rounded">
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">Код:</label>
                            <input type="text" id="code" placeholder="Код из письма" class="w-full border p-2 rounded">
                        </div>
                        <div class="flex gap-3">
                            <button onclick="sendCode()" class="flex-1 bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600">
                                <i class="fas fa-envelope mr-1"></i> Отправить код
                            </button>
                            <button onclick="login()" class="flex-1 bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                                <i class="fas fa-sign-in-alt mr-1"></i> Войти
                            </button>
                        </div>
                    </div>
                    <div id="message" class="mt-4"></div>
                </div>
            </div>
            
            <!-- Админ панель -->
            <div id="adminPanel" style="display:none;">
                <div class="flex gap-2 mb-4 flex-wrap">
                    <button onclick="showDashboard()" class="bg-green-500 text-white px-3 py-2 rounded hover:bg-green-600 text-sm"> Дашборд</button>
                    <button onclick="showLines()" class="bg-blue-500 text-white px-3 py-2 rounded hover:bg-blue-600 text-sm"> Линии</button>
                    <button onclick="showTrains()" class="bg-indigo-500 text-white px-3 py-2 rounded hover:bg-indigo-600 text-sm"> Поезда</button>
                    <button onclick="showWagons()" class="bg-purple-500 text-white px-3 py-2 rounded hover:bg-purple-600 text-sm"> Вагоны</button>
                    <button onclick="showCategories()" class="bg-yellow-500 text-white px-3 py-2 rounded hover:bg-yellow-600 text-sm"> Категории</button>
                    <button onclick="showUsers()" class="bg-teal-500 text-white px-3 py-2 rounded hover:bg-teal-600 text-sm"> Пользователи</button>
                    <button onclick="showPhotos()" class="bg-pink-500 text-white px-3 py-2 rounded hover:bg-pink-600 text-sm"> Фото</button>
                    <button onclick="logout()" class="bg-red-500 text-white px-3 py-2 rounded hover:bg-red-600 text-sm ml-auto"> Выйти</button>
                </div>
                <div id="content" class="mt-4"></div>
            </div>
        </div>
    </div>
    
    <script>
    let token = localStorage.getItem('token');
    const API = window.location.origin;

    window.showMessage = function(msg, color) {
        const div = document.getElementById('message');

        if (!div) {
            alert(msg);
            return;
        }

        div.innerHTML = `
            <div class="p-2 rounded ${
                color === 'green'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-red-100 text-red-700'
            }">${msg}</div>
        `;

        setTimeout(() => div.innerHTML = '', 5000);
    };

    window.sendCode = async function() {
        const emailInput = document.getElementById('email');
        const email = emailInput ? emailInput.value.trim() : '';

        if (!email) {
            window.showMessage('Введите email', 'red');
            return;
        }

        try {
            const resp = await fetch(API + '/api/auth/send-code', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ email })
            });

            const rawText = await resp.text();

            let data;
            try {
                data = JSON.parse(rawText);
            } catch {
                data = { detail: rawText };
            }

            console.log('send-code response:', resp.status, data);

            if (resp.ok && data.ok) {
                window.showMessage(' Код отправлен! Проверьте почту и спам', 'green');
            } else {
                window.showMessage(
                    ' Ошибка отправки кода: ' + 
                    (data.detail || data.message || JSON.stringify(data)),
                    'red'
                );
            }
        } catch (e) {
            console.error('sendCode error:', e);
            window.showMessage(' Ошибка JS/fetch: ' + e.message, 'red');
        }
    };

    window.login = async function() {
        const emailInput = document.getElementById('email');
        const codeInput = document.getElementById('code');

        const email = emailInput ? emailInput.value.trim() : '';
        const code = codeInput ? codeInput.value.trim() : '';

        if (!email || !code) {
            window.showMessage('Введите email и код', 'red');
            return;
        }

        try {
            const resp = await fetch(API + '/api/auth/verify', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ email, code })
            });

            const rawText = await resp.text();

            let data;
            try {
                data = JSON.parse(rawText);
            } catch {
                data = { detail: rawText };
            }

            console.log('verify response:', resp.status, data);

            if (resp.ok && data.access_token) {
                localStorage.setItem('token', data.access_token);
                token = data.access_token;

                document.getElementById('loginPanel').style.display = 'none';
                document.getElementById('adminPanel').style.display = 'block';

                showDashboard();
            } else {
                window.showMessage(
                    ' ' + (data.detail || data.message || 'Неверный код'),
                    'red'
                );
            }
        } catch (e) {
            console.error('login error:', e);
            window.showMessage(' Ошибка JS/fetch: ' + e.message, 'red');
        }
    };

    if (token) {
        document.getElementById('loginPanel').style.display = 'none';
        document.getElementById('adminPanel').style.display = 'block';
        showDashboard();
    }

    // Данные для хранения поездов и вагонов
    let trainsData = [];
    let wagonsData = [];
    let linesData = [];
        
        async function sendCode() {
            const email = document.getElementById('email').value;
            if (!email) { showMessage('Введите email', 'red'); return; }
            
            try {
                const resp = await fetch(API + '/api/auth/send-code', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email})
                });
                const data = await resp.json();
                if (data.ok) {
                    showMessage(' Код отправлен! Проверьте почту', 'green');
                } else {
                    showMessage(' Ошибка', 'red');
                }
            } catch(e) { showMessage(' ' + e.message, 'red'); }
        }
        
        async function login() {
            const email = document.getElementById('email').value;
            const code = document.getElementById('code').value;
            
            try {
                const resp = await fetch(API + '/api/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email, code})
                });
                const data = await resp.json();
                if (data.access_token) {
                    localStorage.setItem('token', data.access_token);
                    token = data.access_token;
                    document.getElementById('loginPanel').style.display = 'none';
                    document.getElementById('adminPanel').style.display = 'block';
                    showDashboard();
                } else {
                    showMessage(' ' + (data.detail || 'Неверный код'), 'red');
                }
            } catch(e) { showMessage(' ' + e.message, 'red'); }
        }
        
        function showMessage(msg, color) {
            const div = document.getElementById('message');
            div.innerHTML = `<div class="p-2 rounded ${color === 'green' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}">${msg}</div>`;
            setTimeout(() => div.innerHTML = '', 3000);
        }
        
        async function logout() {
            localStorage.removeItem('token');
            location.reload();
        }
        
        async function apiCall(url, options = {}) {
            const headers = {
                'Authorization': `Bearer ${token}`,
                ...options.headers
            };

            if (!(options.body instanceof FormData)) {
                headers['Content-Type'] = 'application/json';
            }

            const resp = await fetch(API + url, {
                ...options,
                headers
            });

            const rawText = await resp.text();

            let data;
            try {
                data = JSON.parse(rawText);
            } catch {
                data = { detail: rawText };
            }

            if (!resp.ok) {
                throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
            }

            return data;
        }
        
        async function showDashboard() {
            const content = document.getElementById('content');
            content.innerHTML = '<div class="text-center"><i class="fas fa-spinner fa-spin text-2xl"></i><p>Загрузка...</p></div>';
            
            const [user, lines, categories] = await Promise.all([
                apiCall('/api/auth/me'),
                fetch(API + '/api/lines').then(r => r.json()),
                fetch(API + '/api/categories').then(r => r.json())
            ]);
            
            // Загружаем поезда и вагоны
            await loadTrainsData();
            await loadWagonsData();
            
            content.innerHTML = `
                <div class="bg-green-100 p-4 rounded-lg mb-4"> ${user.email}</div>
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                    <div class="bg-blue-100 p-4 rounded text-center"><div class="text-2xl font-bold">${lines.length}</div><div>Линий</div></div>
                    <div class="bg-indigo-100 p-4 rounded text-center"><div class="text-2xl font-bold">${trainsData.length}</div><div>Поездов</div></div>
                    <div class="bg-purple-100 p-4 rounded text-center"><div class="text-2xl font-bold">${wagonsData.length}</div><div>Вагонов</div></div>
                    <div class="bg-yellow-100 p-4 rounded text-center"><div class="text-2xl font-bold">${categories.length}</div><div>Категорий</div></div>
                </div>
            `;
        }
        
        // ========== ЛИНИИ ==========
        async function showLines() {
            const lines = await fetch(API + '/api/lines').then(r => r.json());
            const content = document.getElementById('content');
            content.innerHTML = `
                <h2 class="text-xl font-bold mb-4"> Линии метро</h2>
                <div class="grid gap-3">
                    ${lines.map(l => `
                        <div class="border rounded p-3 flex items-center gap-3">
                            <div style="width:40px;height:40px;background:#${l.hex};border-radius:8px"></div>
                            <div><b>Линия ${l.number}</b><br><span class="text-sm text-gray-600">${l.name}</span></div>
                            <div class="ml-auto text-sm text-gray-500">ID: ${l.id_line}</div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        // ========== ПОЕЗДА ==========
        async function loadTrainsData() {
            try {
                const resp = await fetch(API + '/api/admin/trains');
                if (resp.ok) trainsData = await resp.json();
                else trainsData = [];
            } catch(e) { trainsData = []; }
            return trainsData;
        }
        
        async function showTrains() {
            await loadTrainsData();
            const lines = await fetch(API + '/api/lines').then(r => r.json());
            
            const content = document.getElementById('content');
            content.innerHTML = `
                <h2 class="text-xl font-bold mb-4"> Управление поездами</h2>
                
                <div class="bg-gray-100 p-4 rounded mb-4">
                    <h3 class="font-bold mb-2"> Добавить поезд</h3>
                    <div class="flex gap-2 flex-wrap">
                        <input type="text" id="newTrainNumber" placeholder="Номер поезда" class="border p-2 rounded">
                        <select id="newTrainLine" class="border p-2 rounded">
                            <option value="">Выберите линию</option>
                            ${lines.map(l => `<option value="${l.id_line}">Линия ${l.number} - ${l.name}</option>`).join('')}
                        </select>
                        <button onclick="createTrain()" class="bg-green-500 text-white px-4 py-2 rounded"> Добавить</button>
                    </div>
                </div>
                
                <div class="grid gap-3">
                    ${trainsData.map(t => `
                        <div class="border rounded p-3">
                            <div class="flex justify-between items-center">
                                <div>
                                    <span class="font-bold">Поезд ${t.number}</span>
                                    <span class="text-sm text-gray-500 ml-2">ID: ${t.id_train}</span>
                                    <div class="text-sm">Линия: ${t.line_name || 'Не назначена'}</div>
                                </div>
                                <div class="flex gap-2">
                                    <select id="lineSelect_${t.id_train}" class="border p-1 rounded text-sm">
                                        <option value="">Сменить линию</option>
                                        ${lines.map(l => `<option value="${l.id_line}" ${t.id_line == l.id_line ? 'selected' : ''}>Линия ${l.number}</option>`).join('')}
                                    </select>
                                    <button onclick="updateTrainLine(${t.id_train})" class="bg-blue-500 text-white px-2 py-1 rounded text-sm"></button>
                                </div>
                            </div>
                            <div class="mt-2 pt-2 border-t">
                                <div class="text-sm font-bold mb-1">Вагоны в составе:</div>
                                <div id="wagonsList_${t.id_train}" class="flex flex-wrap gap-1"></div>
                                <div class="mt-2 flex gap-2">
                                    <input type="text" id="newWagon_${t.id_train}" placeholder="Номер вагона" class="border p-1 rounded text-sm w-32">
                                    <button onclick="addWagonToTrain(${t.id_train})" class="bg-green-500 text-white px-2 py-1 rounded text-sm">+ Добавить вагон</button>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                    ${trainsData.length === 0 ? '<div class="text-center text-gray-500 py-4">Нет поездов. Добавьте первый!</div>' : ''}
                </div>
            `;
            
            // Загружаем вагоны для каждого поезда
            await loadWagonsData();
            for (let train of trainsData) {
                updateWagonsList(train.id_train);
            }
        }
        
        async function createTrain() {
            const number = document.getElementById('newTrainNumber').value;
            const line_id = document.getElementById('newTrainLine').value;
            if (!number) { alert('Введите номер поезда'); return; }
            
            await apiCall('/api/admin/trains', {
                method: 'POST',
                body: JSON.stringify({number, line_id: line_id ? parseInt(line_id) : null})
            });
            showTrains();
        }
        
        async function updateTrainLine(trainId) {
            const select = document.getElementById(`lineSelect_${trainId}`);
            const newLineId = select.value;
            if (!newLineId) { alert('Выберите линию'); return; }
            
            await apiCall(`/api/admin/trains/${trainId}/line`, {
                method: 'PATCH',
                body: JSON.stringify({new_id_line: parseInt(newLineId)})
            });
            showTrains();
        }
        
        async function deleteTrain(trainId) {
            if (!confirm('Удалить поезд?')) return;
            await apiCall(`/api/admin/trains/${trainId}`, { method: 'DELETE' });
            showTrains();
        }
        
        // ========== ВАГОНЫ ==========
        async function loadWagonsData() {
            try {
                const resp = await fetch(API + '/api/admin/wagons');
                if (resp.ok) wagonsData = await resp.json();
                else wagonsData = [];
            } catch(e) { wagonsData = []; }
            return wagonsData;
        }
        
        async function showWagons() {
            await loadWagonsData();
            await loadTrainsData();
            
            const content = document.getElementById('content');
            content.innerHTML = `
                <h2 class="text-xl font-bold mb-4"> Управление вагонами</h2>
                
                <div class="bg-gray-100 p-4 rounded mb-4">
                    <h3 class="font-bold mb-2"> Добавить вагон</h3>
                    <div class="flex gap-2 flex-wrap">
                        <input type="text" id="newWagonNumber" placeholder="Номер вагона" class="border p-2 rounded">
                        <select id="newWagonTrain" class="border p-2 rounded">
                            <option value="">Выберите поезд</option>
                            ${trainsData.map(t => `<option value="${t.id_train}">Поезд ${t.number}</option>`).join('')}
                        </select>
                        <button onclick="createWagon()" class="bg-green-500 text-white px-4 py-2 rounded"> Добавить</button>
                    </div>
                </div>
                
                <div class="grid gap-3">
                    ${wagonsData.map(w => `
                        <div class="border rounded p-3 flex justify-between items-center">
                            <div>
                                <span class="font-bold">Вагон ${w.number}</span>
                                <span class="text-sm text-gray-500 ml-2">ID: ${w.id_wagon}</span>
                                <div class="text-sm">Поезд: ${w.train_number || 'Не назначен'} (ID: ${w.id_train || '-'})</div>
                            </div>
                            <div class="flex gap-2">
                                <select id="trainSelect_${w.id_wagon}" class="border p-1 rounded text-sm">
                                    <option value="">Переместить в поезд</option>
                                    ${trainsData.map(t => `<option value="${t.id_train}" ${w.id_train == t.id_train ? 'selected' : ''}>Поезд ${t.number}</option>`).join('')}
                                </select>
                                <button onclick="updateWagonTrain(${w.id_wagon})" class="bg-blue-500 text-white px-2 py-1 rounded text-sm"></button>
                            </div>
                        </div>
                    `).join('')}
                    ${wagonsData.length === 0 ? '<div class="text-center text-gray-500 py-4">Нет вагонов. Добавьте первый!</div>' : ''}
                </div>
            `;
        }
        
        async function createWagon() {
            const number = document.getElementById('newWagonNumber').value;
            const train_id = document.getElementById('newWagonTrain').value;
            if (!number) { alert('Введите номер вагона'); return; }
            
            await apiCall('/api/admin/wagons', {
                method: 'POST',
                body: JSON.stringify({number, train_id: train_id ? parseInt(train_id) : null})
            });
            showWagons();
        }
        
        async function updateWagonTrain(wagonId) {
            const select = document.getElementById(`trainSelect_${wagonId}`);
            const value = select.value;

            const newTrainId = value ? parseInt(value) : null;

            try {
                await apiCall(`/api/admin/wagons/${wagonId}/train`, {
                    method: 'PATCH',
                    body: JSON.stringify({ new_id_train: newTrainId })
                });

                showWagons();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }
        
        async function deleteWagon(wagonId) {
            if (!confirm('Удалить вагон?')) return;
            await apiCall(`/api/admin/wagons/${wagonId}`, { method: 'DELETE' });
            showWagons();
            showTrains();
        }
        
        async function addWagonToTrain(trainId) {
            const input = document.getElementById(`newWagon_${trainId}`);
            const number = input.value;
            if (!number) { alert('Введите номер вагона'); return; }
            
            await apiCall('/api/admin/wagons', {
                method: 'POST',
                body: JSON.stringify({number, train_id: trainId})
            });
            input.value = '';
            showTrains();
        }
        
        async function updateWagonsList(trainId) {
            const container = document.getElementById(`wagonsList_${trainId}`);
            if (!container) return;
            
            const trainWagons = wagonsData.filter(w => w.id_train === trainId);
            if (trainWagons.length === 0) {
                container.innerHTML = '<span class="text-xs text-gray-400">Нет вагонов</span>';
            } else {
                container.innerHTML = trainWagons.map(w => `
                    <span class="bg-gray-200 px-2 py-1 rounded text-sm inline-flex items-center gap-1">
                         ${w.number}
                        <button onclick="removeWagonFromTrain(${w.id_wagon}, ${trainId})" class="text-red-500 hover:text-red-700 text-xs"></button>
                    </span>
                `).join('');
            }
        }
        
        async function removeWagonFromTrain(wagonId, trainId) {
            if (!confirm('Отвязать вагон от поезда?')) return;

            try {
                await apiCall(`/api/admin/wagons/${wagonId}/train`, {
                    method: 'PATCH',
                    body: JSON.stringify({ new_id_train: null })
                });

                showTrains();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }
        
        // ========== КАТЕГОРИИ ==========
        async function showCategories() {
            const content = document.getElementById('content');

            content.innerHTML = `
                <div class="text-center">
                    <i class="fas fa-spinner fa-spin text-2xl"></i>
                    <p>Загрузка...</p>
                </div>
            `;

            try {
                const cats = await apiCall('/api/admin/categories');

                content.innerHTML = `
                    <h2 class="text-xl font-bold mb-4"> Категории дефектов</h2>

                    <div class="bg-gray-100 p-4 rounded mb-4">
                        <h3 class="font-bold mb-2"> Добавить категорию</h3>

                        <div class="flex gap-2">
                            <input
                                type="text"
                                id="newCategoryName"
                                placeholder="Название категории"
                                class="border p-2 rounded flex-1"
                            >

                            <button
                                onclick="createCategory()"
                                class="bg-green-500 text-white px-4 py-2 rounded"
                            >
                                 Добавить
                            </button>
                        </div>
                    </div>

                    <div class="grid gap-3">
                        ${cats.map(c => `
                            <div class="border rounded p-3 flex items-center gap-2">
                                <div class="text-sm text-gray-500 w-20">
                                    ID: ${c.id_categ}
                                </div>

                                <input
                                    type="text"
                                    id="categoryName_${c.id_categ}"
                                    value="${escapeHtml(c.name)}"
                                    class="border p-2 rounded flex-1"
                                >

                                <button
                                    onclick="updateCategory(${c.id_categ})"
                                    class="bg-blue-500 text-white px-3 py-2 rounded"
                                >
                                     Сохранить
                                </button>
                            </div>
                        `).join('')}
                    </div>
                `;
            } catch (e) {
                content.innerHTML = `
                    <div class="bg-red-100 text-red-700 p-4 rounded">
                        Ошибка загрузки категорий: ${escapeHtml(e.message)}
                    </div>
                `;
            }
        }


        async function createCategory() {
            const input = document.getElementById('newCategoryName');
            const name = input.value.trim();

            if (!name) {
                alert('Введите название категории');
                return;
            }

            try {
                await apiCall('/api/admin/categories', {
                    method: 'POST',
                    body: JSON.stringify({ name })
                });

                showCategories();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }


        async function updateCategory(categoryId) {
            const input = document.getElementById(`categoryName_${categoryId}`);
            const name = input.value.trim();

            if (!name) {
                alert('Название категории не может быть пустым');
                return;
            }

            try {
                await apiCall(`/api/admin/categories/${categoryId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ name })
                });

                showCategories();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }
        
        // ========== ПОЛЬЗОВАТЕЛИ ==========

        async function showUsers() {
            const content = document.getElementById('content');
            content.innerHTML = `
                <div class="text-center">
                    <i class="fas fa-spinner fa-spin text-2xl"></i>
                    <p>Загрузка...</p>
                </div>
            `;

            try {
                const users = await apiCall('/api/admin/users');

                content.innerHTML = `
                    <h2 class="text-xl font-bold mb-4"> Пользователи (${users.length})</h2>
                    <div class="overflow-x-auto">
                        <table class="w-full border-collapse text-sm">
                            <thead>
                                <tr class="bg-gray-100 text-left">
                                    <th class="border px-3 py-2">ID</th>
                                    <th class="border px-3 py-2">Email</th>
                                    <th class="border px-3 py-2">Монеты</th>
                                    <th class="border px-3 py-2">Зарегистрирован</th>
                                    <th class="border px-3 py-2 text-center">Админ</th>
                                    <th class="border px-3 py-2 text-center">Бан</th>
                                    <th class="border px-3 py-2 text-center">Действия</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${users.map(u => `
                                    <tr id="userRow_${u.id_user}" class="${u.banned ? 'bg-red-50' : ''}">
                                        <td class="border px-3 py-2 text-gray-500">${u.id_user}</td>
                                        <td class="border px-3 py-2 font-medium">${escapeHtml(u.email)}</td>
                                        <td class="border px-3 py-2 text-center">${u.coin ?? 0}</td>
                                        <td class="border px-3 py-2 text-gray-500 text-xs">${u.created_at ? u.created_at.slice(0,10) : '-'}</td>
                                        <td class="border px-3 py-2 text-center">
                                            ${u.admin
                                                ? '<span class="bg-blue-100 text-blue-700 px-2 py-0.5 rounded text-xs font-bold"> Админ</span>'
                                                : '<span class="text-gray-400 text-xs">—</span>'
                                            }
                                        </td>
                                        <td class="border px-3 py-2 text-center">
                                            ${u.banned
                                                ? '<span class="bg-red-100 text-red-700 px-2 py-0.5 rounded text-xs font-bold"> Забанен</span>'
                                                : '<span class="text-gray-400 text-xs">—</span>'
                                            }
                                        </td>
                                        <td class="border px-3 py-2">
                                            <div class="flex gap-1 justify-center flex-wrap">
                                                <button
                                                    onclick="toggleUserAdmin(${u.id_user}, ${u.admin})"
                                                    class="${u.admin ? 'bg-gray-400 hover:bg-gray-500' : 'bg-blue-500 hover:bg-blue-600'} text-white px-2 py-1 rounded text-xs"
                                                    title="${u.admin ? 'Снять права админа' : 'Назначить админом'}"
                                                >
                                                    ${u.admin ? ' Снять' : ' Админ'}
                                                </button>
                                                <button
                                                    onclick="toggleUserBan(${u.id_user}, ${u.banned})"
                                                    class="${u.banned ? 'bg-green-500 hover:bg-green-600' : 'bg-red-500 hover:bg-red-600'} text-white px-2 py-1 rounded text-xs"
                                                    title="${u.banned ? 'Разбанить' : 'Забанить'}"
                                                >
                                                    ${u.banned ? ' Разбанить' : ' Бан'}
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                    ${users.length === 0 ? '<div class="text-center text-gray-500 py-8">Пользователей нет</div>' : ''}
                `;
            } catch (e) {
                content.innerHTML = `
                    <div class="bg-red-100 text-red-700 p-4 rounded">
                        Ошибка загрузки пользователей: ${escapeHtml(e.message)}
                    </div>
                `;
            }
        }

        async function toggleUserAdmin(userId, currentAdmin) {
            const newAdmin = !currentAdmin;
            const action = newAdmin ? 'назначить администратором' : 'снять права администратора';
            if (!confirm(`${newAdmin ? 'Назначить' : 'Снять'} права администратора?`)) return;

            try {
                await apiCall(`/api/admin/users/${userId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ admin: newAdmin })
                });
                showUsers();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }

        async function toggleUserBan(userId, currentBanned) {
            const newBanned = !currentBanned;
            if (!confirm(newBanned ? 'Забанить пользователя?' : 'Разбанить пользователя?')) return;

            try {
                await apiCall(`/api/admin/users/${userId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ banned: newBanned })
                });
                showUsers();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }

        // ========== ФОТОГРАФИИ ==========
                        
        function escapeHtml(value) {
            return String(value ?? '-')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#039;');
        }
        async function showPhotos() {
            const content = document.getElementById('content');

            content.innerHTML = `
                <div class="text-center">
                    <i class="fas fa-spinner fa-spin text-2xl"></i>
                    <p>Загрузка...</p>
                </div>
            `;

            try {
                const resp = await fetch(API + '/admin-api/photos');
                const rawText = await resp.text();

                let data;
                try {
                    data = JSON.parse(rawText);
                } catch {
                    throw new Error(rawText);
                }

                if (!resp.ok) {
                    throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
                }

                const photos = data.photos || [];

                if (photos.length === 0) {
                    content.innerHTML = `
                        <div class="text-center py-8">
                            <i class="fas fa-image text-4xl text-gray-400 mb-2"></i>
                            <p>Нет загруженных фотографий</p>
                        </div>
                    `;
                    return;
                }

                content.innerHTML = `
                    <h2 class="text-xl font-bold mb-4"> Все фотографии (${photos.length})</h2>

                    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                        ${photos.map(p => `
                            <div class="border rounded-lg overflow-hidden hover:shadow transition bg-white">
                                <img
                                    src="${p.path}"
                                    class="w-full h-48 object-cover cursor-pointer"
                                    alt="${escapeHtml(p.name)}"
                                    onclick="window.open('${p.path}', '_blank')"
                                >

                                <div class="p-2">
                                    <p class="text-xs truncate font-bold" title="${escapeHtml(p.name)}">
                                        ${escapeHtml(p.name)}
                                    </p>

                                    <div class="text-xs text-gray-700 mt-2 space-y-1">
                                        <p><b>Ветка:</b> ${escapeHtml(p.line_label || p.line || '-')}</p>
                                        <p><b>Поезд:</b> ${escapeHtml(p.train || '-')}</p>
                                        <p><b>Вагон:</b> ${escapeHtml(p.wagon || '-')}</p>
                                        <p><b>Дата/время:</b> ${escapeHtml(p.uploaded_at || '-')}</p>
                                    </div>

                                    <p class="text-xs text-gray-500 mt-2">
                                        ${(p.size / 1024).toFixed(1)} KB
                                    </p>

                                    <button
                                        onclick="deletePhoto('${p.rel_path}')"
                                        class="mt-2 w-full bg-red-500 text-white px-2 py-1 rounded text-xs hover:bg-red-600"
                                    >
                                        <i class="fas fa-trash mr-1"></i> Удалить
                                    </button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            } catch (e) {
                console.error('showPhotos error:', e);

                content.innerHTML = `
                    <div class="bg-red-100 text-red-700 p-4 rounded">
                        <b>Ошибка загрузки фотографий:</b><br>
                        ${escapeHtml(e.message)}
                    </div>
                `;
            }
}
        
        async function deletePhoto(relPath) {
            if (!confirm('Удалить фото?')) return;

            const safePath = relPath
                .split('/')
                .map(encodeURIComponent)
                .join('/');

            await fetch(API + '/admin-api/photos/' + safePath, {
                method: 'DELETE'
            });

            showPhotos();
        }
        
        // ========== ЗАГРУЗКА ФОТО ==========
        function showUpload() {
            document.getElementById('content').innerHTML = `
                <h2 class="text-xl font-bold mb-4"> Загрузка фото дефекта</h2>
                <div class="bg-gray-100 p-6 rounded-lg max-w-2xl mx-auto">
                    <div class="space-y-4">
                        <div>
                            <label class="block font-bold mb-2">Фото:</label>
                            <input type="file" id="foto" accept="image/*" class="border p-2 rounded w-full">
                        </div>
                        <div>
                            <label class="block font-bold mb-2">ID линии:</label>
                            <input type="number" id="line" placeholder="2" class="border p-2 rounded w-full">
                        </div>
                        <div>
                            <label class="block font-bold mb-2">Номер вагона:</label>
                            <input type="text" id="wagon" placeholder="1234" class="border p-2 rounded w-full">
                        </div>
                        <div>
                            <label class="block font-bold mb-2">Номер поезда:</label>
                            <input type="text" id="train" placeholder="5678" class="border p-2 rounded w-full">
                        </div>
                        <div>
                            <label class="block font-bold mb-2">Описание:</label>
                            <textarea id="text_prob" rows="3" placeholder="Опишите дефект..." class="border p-2 rounded w-full"></textarea>
                        </div>
                        <button onclick="uploadPhoto()" class="w-full bg-green-500 text-white px-6 py-2 rounded hover:bg-green-600">
                            <i class="fas fa-cloud-upload-alt mr-1"></i> Отправить
                        </button>
                        <div id="uploadResult"></div>
                    </div>
                </div>
            `;
        }
        
        async function uploadPhoto() {
    const foto = document.getElementById('foto').files[0];
    if (!foto) { alert('Выберите фото'); return; }

    const formData = new FormData();
    formData.append('foto', foto);
    formData.append('line', document.getElementById('line').value);
    formData.append('wagon', document.getElementById('wagon').value);
    formData.append('train', document.getElementById('train').value);
    formData.append('text_prob', document.getElementById('text_prob').value);

    const resultDiv = document.getElementById('uploadResult');
    resultDiv.innerHTML = '<div class="text-blue-600"><i class="fas fa-spinner fa-spin mr-1"></i> Загрузка...</div>';

    try {
        const response = await fetch('/upload-photo', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData
        });

        const rawText = await response.text();

        let data;
        try {
            data = JSON.parse(rawText);
        } catch {
            data = { detail: rawText };
        }

        if (response.ok && data.ok) {
            resultDiv.innerHTML = `
                <div class="bg-green-100 p-3 rounded text-green-700">
                     Фото загружено!<br>
                    <span class="text-sm">Файл: ${data.date_folder}/${data.filename}</span><br>
                    <a href="${data.path}" target="_blank" class="underline text-blue-600">Открыть фото</a>
                </div>
            `;

            document.getElementById('foto').value = '';
            document.getElementById('line').value = '';
            document.getElementById('wagon').value = '';
            document.getElementById('train').value = '';
            document.getElementById('text_prob').value = '';
        } else {
            resultDiv.innerHTML = `
                <div class="bg-red-100 p-3 rounded text-red-700">
                     Ошибка загрузки<br>
                    <span class="text-sm">${data.detail || data.message || 'Неизвестная ошибка'}</span>
                </div>
            `;
        }
    } catch(e) {
        resultDiv.innerHTML = `
            <div class="bg-red-100 p-3 rounded text-red-700">
                 ${e.message}
            </div>
        `;
    }
}
    </script>
</body>
</html>
    """)

@app.get("/api/admin/categories")
async def get_admin_categories():
    pool = await get_db()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id_categ, name::text AS name
            FROM category
            ORDER BY id_categ
        """)

    return [dict(row) for row in rows]


@app.post("/api/admin/categories")
async def create_admin_category(data: CategoryCreate):
    pool = await get_db()

    name = data.name.strip() if data.name else ""

    if not name:
        raise HTTPException(status_code=400, detail="Название категории обязательно")

    async with pool.acquire() as conn:
        column_default = await conn.fetchval("""
            SELECT column_default
            FROM information_schema.columns
            WHERE table_name = 'category'
              AND column_name = 'id_categ'
        """)

        if column_default:
            row = await conn.fetchrow("""
                INSERT INTO category (name)
                VALUES ($1)
                RETURNING id_categ, name::text AS name
            """, name)
        else:
            async with conn.transaction():
                new_id = await conn.fetchval("""
                    SELECT COALESCE(MAX(id_categ), 0) + 1
                    FROM category
                """)

                row = await conn.fetchrow("""
                    INSERT INTO category (id_categ, name)
                    VALUES ($1, $2)
                    RETURNING id_categ, name::text AS name
                """, new_id, name)

    return dict(row)


@app.patch("/api/admin/categories/{category_id}")
async def update_admin_category(category_id: int, data: CategoryUpdate):
    pool = await get_db()

    name = data.name.strip() if data.name else ""

    if not name:
        raise HTTPException(status_code=400, detail="Название категории обязательно")

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE category
            SET name = $1
            WHERE id_categ = $2
            RETURNING id_categ, name::text AS name
        """, name, category_id)

    if not row:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    return dict(row)

# ========== ПОЛЬЗОВАТЕЛИ ==========

@app.get("/api/admin/users")
async def get_admin_users():
    pool = await get_db()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                id_user,
                email::text AS email,
                coin,
                admin,
                COALESCE(banned, false) AS banned,
                created_at
            FROM users
            ORDER BY id_user DESC
        """)

    return [
        {
            **dict(row),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@app.patch("/api/admin/users/{user_id}")
async def update_admin_user(user_id: int, data: UserUpdate):
    pool = await get_db()

    if data.admin is None and data.banned is None:
        raise HTTPException(status_code=400, detail="Нечего обновлять")

    async with pool.acquire() as conn:
        # Убеждаемся, что колонка banned существует
        col_exists = await conn.fetchval("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'banned'
        """)
        if not col_exists:
            await conn.execute("ALTER TABLE users ADD COLUMN banned BOOLEAN DEFAULT false")

        sets = []
        params = []
        idx = 1

        if data.admin is not None:
            sets.append(f"admin = ${idx}")
            params.append(data.admin)
            idx += 1

        if data.banned is not None:
            sets.append(f"banned = ${idx}")
            params.append(data.banned)
            idx += 1

        params.append(user_id)

        row = await conn.fetchrow(
            f"""
            UPDATE users
            SET {', '.join(sets)}
            WHERE id_user = ${idx}
            RETURNING id_user, email::text AS email, admin, COALESCE(banned, false) AS banned
            """,
            *params
        )

    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    return dict(row)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_to_api(request: Request, path: str):
    body = await request.body()
    url = f"{API_BASE}/{path}"
    
    headers = dict(request.headers)
    headers.pop("host", None)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params)
            )
            return JSONResponse(
                status_code=response.status_code,
                content=response.json()
                if "application/json" in response.headers.get("content-type", "")
                else {"status": response.status_code}
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
        
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)