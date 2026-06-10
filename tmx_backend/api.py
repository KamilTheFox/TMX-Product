import hashlib
import os
import secrets
import smtplib
import string
import shutil
from pathlib import Path

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, Header, UploadFile, File, Form
from pydantic import BaseModel, EmailStr
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


load_dotenv()


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

AUTH_SECRET = os.getenv("AUTH_SECRET")
AUTH_CODE_TTL_MINUTES = int(os.getenv("AUTH_CODE_TTL_MINUTES", "10"))
AUTH_SESSION_TTL_DAYS = int(os.getenv("AUTH_SESSION_TTL_DAYS", "30"))
AUTH_MAX_ATTEMPTS = int(os.getenv("AUTH_MAX_ATTEMPTS", "5"))

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "foto")


if not all([DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD]):
    raise RuntimeError("Не все данные для подключения к БД указаны в .env")

if not AUTH_SECRET or AUTH_SECRET == "change_me_to_random_secret":
    raise RuntimeError("Укажи нормальный AUTH_SECRET в .env")


DB_DSN = (
    f"host={DB_HOST} "
    f"port={DB_PORT} "
    f"dbname={DB_NAME} "
    f"user={DB_USER} "
    f"password={DB_PASSWORD}"
)


pool: Optional[ConnectionPool] = None


SQL_NEAREST_LINE = """
WITH p AS (
    SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt
)
SELECT
    l.id_line,
    l.number,
    l.hex,
    l.name,
    ST_Distance(l.geom::geography, p.pt::geography) AS distance_m
FROM lines l, p
ORDER BY
    ST_Distance(l.geom::geography, p.pt::geography) ASC,
    random()
LIMIT 1;
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool

    pool = ConnectionPool(
        conninfo=DB_DSN,
        min_size=1,
        max_size=10,
        kwargs={
            "row_factory": dict_row
        },
        open=False
    )

    pool.open()
    pool.wait()

    yield

    pool.close()


app = FastAPI(
    title="TMX Backend",
    description="Backend для линий, фото и авторизации",
    version="1.0.0",
    lifespan=lifespan
)


class LineResponse(BaseModel):
    id_line: int
    number: Optional[str]
    hex: Optional[str]
    name: str
    distance_m: float


class AuthSendRequest(BaseModel):
    email: EmailStr


class AuthVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class AuthVerifyResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"


class CurrentUser(BaseModel):
    id_user: int
    email: EmailStr
    admin: bool
    ban: bool


class CategoryResponse(BaseModel):
    id_categ: int
    name: str


class LineListResponse(BaseModel):
    id_line: int
    number: Optional[str]
    hex: Optional[str]
    name: str

class TrainShortResponse(BaseModel):
    id_train: int
    number: str


class WagonShortResponse(BaseModel):
    id_wagon: int
    number: str


class UpdateWagonTrainRequest(BaseModel):
    new_id_train: int


class UpdateTrainLineRequest(BaseModel):
    new_id_line: int


class OkResponse(BaseModel):
    ok: bool


def hash_value(value: str) -> str:
    raw = f"{AUTH_SECRET}:{value}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def generate_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def send_auth_code_email(email: str, code: str) -> None:
    """
    Если SMTP не настроен — код просто выводится в консоль.
    Если SMTP настроен — отправляет письмо.
    """

    if not SMTP_HOST:
        print("=" * 50, flush=True)
        print(f"AUTH CODE для {email}: {code}", flush=True)
        print("=" * 50, flush=True)
        return

    sender = SMTP_FROM or SMTP_USER

    if not sender:
        raise RuntimeError("SMTP_FROM или SMTP_USER не указан в .env")

    message = EmailMessage()
    message["Subject"] = "Код авторизации"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Ваш код авторизации: {code}\n\n"
        f"Код действует {AUTH_CODE_TTL_MINUTES} минут.\n\n"
        f"Если вы не запрашивали код, просто проигнорируйте это письмо."
    )

    timeout_seconds = 10

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            SMTP_HOST,
            SMTP_PORT,
            timeout=timeout_seconds
        ) as smtp:
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)

            smtp.send_message(message)
    else:
        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=timeout_seconds
        ) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()

            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)

            smtp.send_message(message)


def get_current_user(
    authorization: str = Header(..., alias="Authorization")
) -> CurrentUser:
    """
    Проверяет Authorization: Bearer <token>
    и возвращает текущего пользователя.
    Если пользователь забанен, авторизация по токену запрещается.
    """

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Нужен заголовок Authorization: Bearer <token>"
        )

    token = authorization.replace("Bearer ", "", 1).strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Пустой токен"
        )

    token_hash = hash_value(token)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        u.id_user,
                        u.email,
                        u.admin,
                        u.ban
                    FROM auth_sessions s
                    JOIN users u ON u.id_user = s.id_user
                    WHERE s.token_hash = %s
                      AND s.expires_at > now()
                    LIMIT 1;
                    """,
                    (token_hash,)
                )

                user = cur.fetchone()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка проверки токена: {str(e)}"
        )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Неверный или истёкший токен"
        )

    if user["ban"]:
        raise HTTPException(
            status_code=403,
            detail="ban"
        )

    return CurrentUser(
        id_user=user["id_user"],
        email=user["email"],
        admin=user["admin"],
        ban=user["ban"]
    )

def require_admin(
    current_user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """
    Пропускает только админов.
    """

    if not current_user.admin:
        raise HTTPException(
            status_code=403,
            detail="Доступ разрешён только администратору"
        )

    return current_user

def get_line_id(cur, line_value: str) -> int:
    line_value = line_value.strip()

    if not line_value:
        raise HTTPException(
            status_code=400,
            detail="line не может быть пустым"
        )

    cur.execute(
        """
        SELECT id_line
        FROM lines
        WHERE number = %s
           OR name ILIKE %s
           OR id_line::TEXT = %s
        LIMIT 1;
        """,
        (
            line_value,
            line_value,
            line_value
        )
    )

    row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Линия не найдена: {line_value}"
        )

    return row["id_line"]


def get_or_create_train(cur, train_number: str, id_line: int) -> int:
    train_number = train_number.strip()

    if not train_number:
        raise HTTPException(
            status_code=400,
            detail="train не может быть пустым"
        )

    cur.execute(
        """
        SELECT id_train
        FROM trains
        WHERE number = %s
          AND id_line = %s
        LIMIT 1;
        """,
        (train_number, id_line)
    )

    row = cur.fetchone()

    if row:
        return row["id_train"]

    cur.execute(
        """
        INSERT INTO trains (number, id_line)
        VALUES (%s, %s)
        RETURNING id_train;
        """,
        (train_number, id_line)
    )

    row = cur.fetchone()
    return row["id_train"]


def get_or_create_wagon(cur, wagon_number: str, id_train: int) -> int:
    wagon_number = wagon_number.strip()

    if not wagon_number:
        raise HTTPException(
            status_code=400,
            detail="wagon не может быть пустым"
        )

    cur.execute(
        """
        SELECT id_wagon
        FROM wagons
        WHERE number = %s
          AND id_train = %s
        LIMIT 1;
        """,
        (wagon_number, id_train)
    )

    row = cur.fetchone()

    if row:
        return row["id_wagon"]

    cur.execute(
        """
        INSERT INTO wagons (number, id_train)
        VALUES (%s, %s)
        RETURNING id_wagon;
        """,
        (wagon_number, id_train)
    )

    row = cur.fetchone()
    return row["id_wagon"]


def create_text_prob(cur, text_value: str) -> Optional[int]:
    text_value = text_value.strip()

    if not text_value:
        return None

    cur.execute(
        """
        INSERT INTO text_prob (text)
        VALUES (%s)
        RETURNING id_text;
        """,
        (text_value,)
    )

    row = cur.fetchone()
    return row["id_text"]


def get_default_category_id(cur) -> int:
    cur.execute(
        """
        INSERT INTO category (name)
        VALUES ('Не определено')
        ON CONFLICT (name)
        DO UPDATE SET name = EXCLUDED.name
        RETURNING id_categ;
        """
    )

    row = cur.fetchone()
    return row["id_categ"]


@app.get("/lines/{id_line}/trains", response_model=list[TrainShortResponse])
def get_trains_by_line(id_line: int):
    """
    Забрать все активные поезда по id линии.
    Возвращает id поезда и номер поезда.
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id_line
                    FROM lines
                    WHERE id_line = %s;
                    """,
                    (id_line,)
                )

                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Линия с id {id_line} не найдена"
                    )

                cur.execute(
                    """
                    SELECT
                        id_train,
                        number
                    FROM trains
                    WHERE id_line = %s
                      AND activ = true
                    ORDER BY id_train;
                    """,
                    (id_line,)
                )

                rows = cur.fetchall()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении поездов: {str(e)}"
        )

    return rows


@app.get("/trains/{id_train}/wagons", response_model=list[WagonShortResponse])
def get_wagons_by_train(id_train: int):
    """
    Забрать все активные вагоны по id поезда.
    Возвращает id вагона и номер вагона.
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id_train
                    FROM trains
                    WHERE id_train = %s
                      AND activ = true;
                    """,
                    (id_train,)
                )

                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Активный поезд с id {id_train} не найден"
                    )

                cur.execute(
                    """
                    SELECT
                        id_wagon,
                        number
                    FROM wagons
                    WHERE id_train = %s
                      AND activ = true
                    ORDER BY id_wagon;
                    """,
                    (id_train,)
                )

                rows = cur.fetchall()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении вагонов поезда: {str(e)}"
        )

    return rows



@app.get("/wagons", response_model=list[WagonShortResponse])
def get_all_wagons():
    """
    Забрать все активные вагоны.
    Возвращает id вагона и номер вагона.
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id_wagon,
                        number
                    FROM wagons
                    WHERE activ = true
                    ORDER BY id_wagon;
                    """
                )

                rows = cur.fetchall()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении вагонов: {str(e)}"
        )

    return rows





@app.get("/categories", response_model=list[CategoryResponse])
def get_categories():
    """
    Вернуть все категории дефектов.
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id_categ,
                        name
                    FROM category
                    ORDER BY id_categ;
                    """
                )

                rows = cur.fetchall()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении категорий: {str(e)}"
        )

    return rows

@app.get("/lines", response_model=list[LineListResponse])
def get_lines():
    """
    Вернуть все линии без геометрии.
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id_line,
                        number,
                        hex,
                        name
                    FROM lines
                    ORDER BY id_line;
                    """
                )

                rows = cur.fetchall()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении линий: {str(e)}"
        )

    return rows


    
@app.get("/auth/me")
def auth_me(
    current_user: CurrentUser = Depends(get_current_user)
):
    return {
        "ok": True,
        "email": current_user.email,
        "admin": current_user.admin
    }


@app.post("/set_foto")
def set_foto(
    foto: UploadFile = File(...),
    line: str = Form(...),
    wagon: str = Form(...),
    train: str = Form(...),
    text_prob: str = Form(""),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Авторизованный пользователь отправляет фото.

    1. Данные сохраняются в defects.
    2. Получаем id записи.
    3. Фото сохраняется как foto/YYYY-MM-DD/{id}.jpg
    4. В ответ возвращаем только ok.
    """

    today_dir = date.today().isoformat()
    upload_dir = Path(UPLOAD_ROOT) / today_dir
    upload_dir.mkdir(parents=True, exist_ok=True)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                id_line = get_line_id(cur, line)
                id_train = get_or_create_train(cur, train, id_line)
                id_wagon = get_or_create_wagon(cur, wagon, id_train)
                id_text = create_text_prob(cur, text_prob)
                id_categ = get_default_category_id(cur)

                cur.execute(
                    """
                    INSERT INTO defects (
                        id_wagon,
                        id_train,
                        id_text,
                        id_categ,
                        id_line
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        id_wagon,
                        id_train,
                        id_text,
                        id_categ,
                        id_line
                    )
                )

                defect = cur.fetchone()
                defect_id = defect["id"]

                file_path = upload_dir / f"{defect_id}.jpg"

                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(foto.file, buffer)

            conn.commit()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при сохранении фото: {str(e)}"
        )

    return {
        "ok": True
    }


@app.get("/get_line", response_model=LineResponse)
def get_line(
    x: float = Query(..., description="Долгота"),
    y: float = Query(..., description="Широта")
):
    if not (-180 <= x <= 180):
        raise HTTPException(
            status_code=400,
            detail="x должен быть долготой от -180 до 180"
        )

    if not (-90 <= y <= 90):
        raise HTTPException(
            status_code=400,
            detail="y должен быть широтой от -90 до 90"
        )

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SQL_NEAREST_LINE, (x, y))
                row = cur.fetchone()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при запросе к БД: {str(e)}"
        )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="В таблице lines нет линий"
        )

    return {
        "id_line": row["id_line"],
        "number": row["number"],
        "hex": row["hex"],
        "name": row["name"],
        "distance_m": round(float(row["distance_m"]), 2)
    }


@app.post("/auth/send-code")
def auth_send_code(data: AuthSendRequest):
    email = normalize_email(str(data.email))
    code = generate_code()
    code_hash = hash_value(code)

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=AUTH_CODE_TTL_MINUTES
    )

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ban
                    FROM users
                    WHERE email = %s
                    LIMIT 1;
                    """,
                    (email,)
                )

                user = cur.fetchone()

                if user is not None and user["ban"]:
                    raise HTTPException(
                        status_code=403,
                        detail="ban"
                    )

                cur.execute(
                    """
                    UPDATE auth_codes
                    SET used = true
                    WHERE email = %s
                      AND used = false;
                    """,
                    (email,)
                )

                cur.execute(
                    """
                    INSERT INTO auth_codes (
                        email,
                        code_hash,
                        expires_at
                    )
                    VALUES (%s, %s, %s);
                    """,
                    (email, code_hash, expires_at)
                )

            conn.commit()

        send_auth_code_email(email, code)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при отправке кода: {str(e)}"
        )

    return {
        "ok": True,
        "message": "Код авторизации отправлен"
    }

@app.get("/health")
def health():
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok;")
                row = cur.fetchone()

        return {
            "ok": True,
            "database": row["ok"] == 1
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Backend работает, но БД недоступна: {str(e)}"
        )


@app.post("/auth/verify", response_model=AuthVerifyResponse)
def auth_verify(data: AuthVerifyRequest):
    email = normalize_email(str(data.email))
    code = data.code.strip()

    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="Код должен состоять из 6 цифр")

    code_hash = hash_value(code)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # 1. Создаём/получаем пользователя и сразу проверяем бан
                cur.execute(
                    """
                    INSERT INTO users (email)
                    VALUES (%s)
                    ON CONFLICT (email)
                    DO UPDATE SET email = EXCLUDED.email
                    RETURNING id_user, email, admin, ban;
                    """,
                    (email,)
                )
                user = cur.fetchone()

                if user["ban"]:
                    conn.commit()
                    raise HTTPException(status_code=403, detail="ban")

                # 2. Берём последний активный код  <-- ЭТОГО НЕ ХВАТАЛО
                cur.execute(
                    """
                    SELECT id_code, code_hash, expires_at, attempts
                    FROM auth_codes
                    WHERE email = %s
                      AND used = false
                    ORDER BY expires_at DESC
                    LIMIT 1;
                    """,
                    (email,)
                )
                auth_code = cur.fetchone()

                if auth_code is None:
                    raise HTTPException(status_code=400, detail="Активный код не найден")

                if auth_code["expires_at"] < datetime.now(timezone.utc):
                    cur.execute("UPDATE auth_codes SET used = true WHERE id_code = %s;",
                                (auth_code["id_code"],))
                    conn.commit()
                    raise HTTPException(status_code=400, detail="Код истёк")

                if auth_code["attempts"] >= AUTH_MAX_ATTEMPTS:
                    cur.execute("UPDATE auth_codes SET used = true WHERE id_code = %s;",
                                (auth_code["id_code"],))
                    conn.commit()
                    raise HTTPException(status_code=400, detail="Превышено количество попыток")

                if not secrets.compare_digest(auth_code["code_hash"], code_hash):
                    cur.execute("UPDATE auth_codes SET attempts = attempts + 1 WHERE id_code = %s;",
                                (auth_code["id_code"],))
                    conn.commit()
                    raise HTTPException(status_code=400, detail="Неверный код")

                # 3. Код верный — помечаем использованным
                cur.execute("UPDATE auth_codes SET used = true WHERE id_code = %s;",
                            (auth_code["id_code"],))

                # 4. Создаём сессию
                token = generate_token()
                token_hash = hash_value(token)
                session_expires_at = datetime.now(timezone.utc) + timedelta(days=AUTH_SESSION_TTL_DAYS)

                cur.execute(
                    """
                    INSERT INTO auth_sessions (id_user, token_hash, expires_at)
                    VALUES (%s, %s, %s);
                    """,
                    (user["id_user"], token_hash, session_expires_at)
                )

            conn.commit()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при проверке кода: {str(e)}")

    return {"access_token": token, "token_type": "Bearer"}