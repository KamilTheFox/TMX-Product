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
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool

    pool = ConnectionPool(
        conninfo=DB_DSN,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=False,
    )

    pool.open()
    pool.wait()

    yield

    pool.close()


app = FastAPI(
    title="TMX Backend",
    description="Backend: авторизация пользователя, отправка фото, категории",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------- Pydantic-модели ----------

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
    ban: bool


class CategoryResponse(BaseModel):
    id_categ: int
    name: str


class WagonShortResponse(BaseModel):
    id_wagon: int
    number: str


class OkResponse(BaseModel):
    ok: bool


# ---------- Утилиты ----------

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
    Если SMTP не настроен — код выводится в консоль.
    Если настроен — отправляется письмо.
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
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout_seconds) as smtp:
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout_seconds) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)


def get_current_user(
    authorization: str = Header(..., alias="Authorization")
) -> CurrentUser:
    """
    Проверяет Authorization: Bearer <token> и возвращает пользователя.
    Забаненный пользователь не проходит.
    """

    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нужен заголовок Authorization: Bearer <token>")

    token = authorization.replace("Bearer ", "", 1).strip()
    if not token:
        raise HTTPException(401, "Пустой токен")

    token_hash = hash_value(token)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id_user, u.email, u.ban
                    FROM auth_sessions s
                    JOIN users u ON u.id_user = s.id_user
                    WHERE s.token_hash = %s
                      AND s.expires_at > now()
                    LIMIT 1;
                    """,
                    (token_hash,),
                )
                user = cur.fetchone()
    except Exception as e:
        raise HTTPException(500, f"Ошибка проверки токена: {str(e)}")

    if user is None:
        raise HTTPException(401, "Неверный или истёкший токен")

    if user["ban"]:
        raise HTTPException(403, "ban")

    return CurrentUser(id_user=user["id_user"], email=user["email"], ban=user["ban"])


def create_text_prob(cur, text_value: str) -> Optional[int]:
    text_value = (text_value or "").strip()
    if not text_value:
        return None

    cur.execute(
        "INSERT INTO text_prob (text) VALUES (%s) RETURNING id_text;",
        (text_value,),
    )
    return cur.fetchone()["id_text"]


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
    return cur.fetchone()["id_categ"]


def resolve_wagon(cur, wagon_value: str):
    """
    Принимает номер вагона ИЛИ id_wagon (строкой).
    Сначала ищем по номеру (как шлёт клиент), затем по id_wagon.
    Поезд и линия берутся из самого вагона.
    """

    wagon_value = (wagon_value or "").strip()
    if not wagon_value:
        raise HTTPException(400, "wagon не может быть пустым")

    # 1) по номеру вагона
    cur.execute(
        """
        SELECT w.id_wagon, w.id_train, t.id_line
        FROM wagons w
        JOIN trains t ON t.id_train = w.id_train
        WHERE w.number = %s
        ORDER BY w.id_wagon
        LIMIT 1;
        """,
        (wagon_value,),
    )
    row = cur.fetchone()

    if row is None:
        raise HTTPException(404, f"Вагон не найден: {wagon_value}")

    return row["id_wagon"], row["id_train"], row["id_line"]


def resolve_category(cur, category_value: str) -> int:
    """
    Принимает id_categ (числом) ИЛИ название категории из /categories.
    Пустое значение -> дефолтная категория 'Не определено'.
    """

    category_value = (category_value or "").strip()
    if not category_value:
        return get_default_category_id(cur)

    if category_value.isdigit():
        cur.execute(
            "SELECT id_categ FROM category WHERE id_categ = %s LIMIT 1;",
            (int(category_value),),
        )
        row = cur.fetchone()
        if row:
            return row["id_categ"]

    cur.execute(
        "SELECT id_categ FROM category WHERE name ILIKE %s LIMIT 1;",
        (category_value,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"Категория не найдена: {category_value}")

    return row["id_categ"]


# ---------- Эндпоинты ----------

@app.get("/health")
def health():
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok;")
                row = cur.fetchone()
        return {"ok": True, "database": row["ok"] == 1}
    except Exception as e:
        raise HTTPException(500, f"Backend работает, но БД недоступна: {str(e)}")


@app.get("/categories", response_model=list[CategoryResponse])
def get_categories():
    """
    Вернуть все категории дефектов. (Оставлено без изменений.)
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id_categ, name FROM category ORDER BY id_categ;")
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(500, f"Ошибка при получении категорий: {str(e)}")

    return rows


@app.get("/wagons", response_model=list[WagonShortResponse])
def get_all_wagons():
    """
    Список активных вагонов. Нужен клиенту, чтобы выбрать вагон
    и прислать его id_wagon в /set_foto.
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id_wagon, number
                    FROM wagons
                    WHERE activ = true
                    ORDER BY id_wagon;
                    """
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(500, f"Ошибка при получении вагонов: {str(e)}")

    return rows


@app.post("/auth/send-code", response_model=OkResponse)
def auth_send_code(data: AuthSendRequest):
    email = normalize_email(str(data.email))
    code = generate_code()
    code_hash = hash_value(code)

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=AUTH_CODE_TTL_MINUTES)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ban FROM users WHERE email = %s LIMIT 1;",
                    (email,),
                )
                user = cur.fetchone()
                if user is not None and user["ban"]:
                    raise HTTPException(403, "ban")

                cur.execute(
                    """
                    UPDATE auth_codes
                    SET used = true
                    WHERE email = %s AND used = false;
                    """,
                    (email,),
                )

                cur.execute(
                    """
                    INSERT INTO auth_codes (email, code_hash, expires_at)
                    VALUES (%s, %s, %s);
                    """,
                    (email, code_hash, expires_at),
                )

            conn.commit()

        send_auth_code_email(email, code)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Ошибка при отправке кода: {str(e)}")

    return {"ok": True}


@app.post("/auth/verify", response_model=AuthVerifyResponse)
def auth_verify(data: AuthVerifyRequest):
    email = normalize_email(str(data.email))
    code = data.code.strip()

    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "Код должен состоять из 6 цифр")

    code_hash = hash_value(code)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # создаём/получаем пользователя и проверяем бан
                cur.execute(
                    """
                    INSERT INTO users (email)
                    VALUES (%s)
                    ON CONFLICT (email)
                    DO UPDATE SET email = EXCLUDED.email
                    RETURNING id_user, email, ban;
                    """,
                    (email,),
                )
                user = cur.fetchone()

                if user["ban"]:
                    conn.commit()
                    raise HTTPException(403, "ban")

                # берём последний активный код (этого запроса не хватало раньше)
                cur.execute(
                    """
                    SELECT id_code, code_hash, expires_at, attempts
                    FROM auth_codes
                    WHERE email = %s AND used = false
                    ORDER BY expires_at DESC
                    LIMIT 1;
                    """,
                    (email,),
                )
                auth_code = cur.fetchone()

                if auth_code is None:
                    raise HTTPException(400, "Активный код не найден")

                if auth_code["expires_at"] < datetime.now(timezone.utc):
                    cur.execute(
                        "UPDATE auth_codes SET used = true WHERE id_code = %s;",
                        (auth_code["id_code"],),
                    )
                    conn.commit()
                    raise HTTPException(400, "Код истёк")

                if auth_code["attempts"] >= AUTH_MAX_ATTEMPTS:
                    cur.execute(
                        "UPDATE auth_codes SET used = true WHERE id_code = %s;",
                        (auth_code["id_code"],),
                    )
                    conn.commit()
                    raise HTTPException(400, "Превышено количество попыток")

                if not secrets.compare_digest(auth_code["code_hash"], code_hash):
                    cur.execute(
                        "UPDATE auth_codes SET attempts = attempts + 1 WHERE id_code = %s;",
                        (auth_code["id_code"],),
                    )
                    conn.commit()
                    raise HTTPException(400, "Неверный код")

                # код верный — гасим его
                cur.execute(
                    "UPDATE auth_codes SET used = true WHERE id_code = %s;",
                    (auth_code["id_code"],),
                )

                # создаём сессию
                token = generate_token()
                token_hash = hash_value(token)
                session_expires_at = datetime.now(timezone.utc) + timedelta(
                    days=AUTH_SESSION_TTL_DAYS
                )

                cur.execute(
                    """
                    INSERT INTO auth_sessions (id_user, token_hash, expires_at)
                    VALUES (%s, %s, %s);
                    """,
                    (user["id_user"], token_hash, session_expires_at),
                )

            conn.commit()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Ошибка при проверке кода: {str(e)}")

    return {"access_token": token, "token_type": "Bearer"}


@app.get("/auth/me")
def auth_me(current_user: CurrentUser = Depends(get_current_user)):
    return {"ok": True, "email": current_user.email}


@app.post("/set_foto", response_model=OkResponse)
def set_foto(
    foto: UploadFile = File(...),
    wagon: str = Form(...),
    category: str = Form(...),
    text_prob: str = Form(""),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Авторизованный пользователь присылает фото.

    Поля формы:
      foto      — файл изображения
      wagon     — id_wagon (число из /wagons)
      category  — id_categ (число) или название категории из /categories
      text_prob — необязательный комментарий

    Поезд и линия определяются автоматически по выбранному вагону.
    Фото сохраняется как foto/YYYY-MM-DD/{id}.jpg
    """

    today_dir = date.today().isoformat()
    upload_dir = Path(UPLOAD_ROOT) / today_dir
    upload_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = None

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                id_wagon, id_train, id_line = resolve_wagon(cur, wagon)
                id_categ = resolve_category(cur, category)
                id_text = create_text_prob(cur, text_prob)

                cur.execute(
                    """
                    INSERT INTO defects (
                        id_wagon,
                        id_train,
                        id_text,
                        id_categ,
                        id_line,
                        id_user
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        id_wagon,
                        id_train,
                        id_text,
                        id_categ,
                        id_line,
                        current_user.id_user,
                    ),
                )
                defect_id = cur.fetchone()["id"]

                # пишем во временный файл, переименуем после успешного commit,
                # чтобы не оставлять "осиротевший" файл при ошибке БД
                file_path = upload_dir / f"{defect_id}.jpg"
                tmp_path = upload_dir / f".{defect_id}.tmp"
                with open(tmp_path, "wb") as buffer:
                    shutil.copyfileobj(foto.file, buffer)

            conn.commit()

        os.replace(tmp_path, file_path)
        tmp_path = None

    except HTTPException:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Ошибка при сохранении фото: {str(e)}")

    return {"ok": True}
    
    
    
    
# import hashlib
# import os
# import secrets
# import smtplib
# import string
# import shutil
# from pathlib import Path

# from contextlib import asynccontextmanager
# from datetime import date, datetime, timedelta, timezone
# from email.message import EmailMessage
# from typing import Optional

# from dotenv import load_dotenv
# from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form
# from pydantic import BaseModel, EmailStr
# from psycopg.rows import dict_row
# from psycopg_pool import ConnectionPool


# load_dotenv()


# DB_HOST = os.getenv("DB_HOST")
# DB_PORT = os.getenv("DB_PORT")
# DB_NAME = os.getenv("DB_NAME")
# DB_USER = os.getenv("DB_USER")
# DB_PASSWORD = os.getenv("DB_PASSWORD")

# AUTH_SECRET = os.getenv("AUTH_SECRET")
# AUTH_CODE_TTL_MINUTES = int(os.getenv("AUTH_CODE_TTL_MINUTES", "10"))
# AUTH_SESSION_TTL_DAYS = int(os.getenv("AUTH_SESSION_TTL_DAYS", "30"))
# AUTH_MAX_ATTEMPTS = int(os.getenv("AUTH_MAX_ATTEMPTS", "5"))

# SMTP_HOST = os.getenv("SMTP_HOST", "")
# SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
# SMTP_USER = os.getenv("SMTP_USER", "")
# SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# SMTP_FROM = os.getenv("SMTP_FROM", "")
# SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
# SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

# UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "foto")


# if not all([DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD]):
#     raise RuntimeError("Не все данные для подключения к БД указаны в .env")

# if not AUTH_SECRET or AUTH_SECRET == "change_me_to_random_secret":
#     raise RuntimeError("Укажи нормальный AUTH_SECRET в .env")


# DB_DSN = (
#     f"host={DB_HOST} "
#     f"port={DB_PORT} "
#     f"dbname={DB_NAME} "
#     f"user={DB_USER} "
#     f"password={DB_PASSWORD}"
# )


# pool: Optional[ConnectionPool] = None


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global pool

#     pool = ConnectionPool(
#         conninfo=DB_DSN,
#         min_size=1,
#         max_size=10,
#         kwargs={"row_factory": dict_row},
#         open=False,
#     )

#     pool.open()
#     pool.wait()

#     yield

#     pool.close()


# app = FastAPI(
#     title="TMX Backend",
#     description="Backend: авторизация пользователя, отправка фото, категории",
#     version="2.0.0",
#     lifespan=lifespan,
# )


# # ---------- Pydantic-модели ----------

# class AuthSendRequest(BaseModel):
#     email: EmailStr


# class AuthVerifyRequest(BaseModel):
#     email: EmailStr
#     code: str


# class AuthVerifyResponse(BaseModel):
#     access_token: str
#     token_type: str = "Bearer"


# class CurrentUser(BaseModel):
#     id_user: int
#     email: EmailStr
#     ban: bool


# class CategoryResponse(BaseModel):
#     id_categ: int
#     name: str


# class WagonShortResponse(BaseModel):
#     id_wagon: int
#     number: str


# class OkResponse(BaseModel):
#     ok: bool


# # ---------- Утилиты ----------

# def hash_value(value: str) -> str:
#     raw = f"{AUTH_SECRET}:{value}".encode("utf-8")
#     return hashlib.sha256(raw).hexdigest()


# def normalize_email(email: str) -> str:
#     return email.strip().lower()


# def generate_code() -> str:
#     return "".join(secrets.choice(string.digits) for _ in range(6))


# def generate_token() -> str:
#     return secrets.token_urlsafe(32)


# def send_auth_code_email(email: str, code: str) -> None:
#     """
#     Если SMTP не настроен — код выводится в консоль.
#     Если настроен — отправляется письмо.
#     """

#     if not SMTP_HOST:
#         print("=" * 50, flush=True)
#         print(f"AUTH CODE для {email}: {code}", flush=True)
#         print("=" * 50, flush=True)
#         return

#     sender = SMTP_FROM or SMTP_USER
#     if not sender:
#         raise RuntimeError("SMTP_FROM или SMTP_USER не указан в .env")

#     message = EmailMessage()
#     message["Subject"] = "Код авторизации"
#     message["From"] = sender
#     message["To"] = email
#     message.set_content(
#         f"Ваш код авторизации: {code}\n\n"
#         f"Код действует {AUTH_CODE_TTL_MINUTES} минут.\n\n"
#         f"Если вы не запрашивали код, просто проигнорируйте это письмо."
#     )

#     timeout_seconds = 10

#     if SMTP_USE_SSL:
#         with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout_seconds) as smtp:
#             if SMTP_USER and SMTP_PASSWORD:
#                 smtp.login(SMTP_USER, SMTP_PASSWORD)
#             smtp.send_message(message)
#     else:
#         with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout_seconds) as smtp:
#             if SMTP_USE_TLS:
#                 smtp.starttls()
#             if SMTP_USER and SMTP_PASSWORD:
#                 smtp.login(SMTP_USER, SMTP_PASSWORD)
#             smtp.send_message(message)


# def get_current_user(
#     authorization: str = Header(..., alias="Authorization")
# ) -> CurrentUser:
#     """
#     Проверяет Authorization: Bearer <token> и возвращает пользователя.
#     Забаненный пользователь не проходит.
#     """

#     if not authorization.startswith("Bearer "):
#         raise HTTPException(401, "Нужен заголовок Authorization: Bearer <token>")

#     token = authorization.replace("Bearer ", "", 1).strip()
#     if not token:
#         raise HTTPException(401, "Пустой токен")

#     token_hash = hash_value(token)

#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     """
#                     SELECT u.id_user, u.email, u.ban
#                     FROM auth_sessions s
#                     JOIN users u ON u.id_user = s.id_user
#                     WHERE s.token_hash = %s
#                       AND s.expires_at > now()
#                     LIMIT 1;
#                     """,
#                     (token_hash,),
#                 )
#                 user = cur.fetchone()
#     except Exception as e:
#         raise HTTPException(500, f"Ошибка проверки токена: {str(e)}")

#     if user is None:
#         raise HTTPException(401, "Неверный или истёкший токен")

#     if user["ban"]:
#         raise HTTPException(403, "ban")

#     return CurrentUser(id_user=user["id_user"], email=user["email"], ban=user["ban"])


# def create_text_prob(cur, text_value: str) -> Optional[int]:
#     text_value = (text_value or "").strip()
#     if not text_value:
#         return None

#     cur.execute(
#         "INSERT INTO text_prob (text) VALUES (%s) RETURNING id_text;",
#         (text_value,),
#     )
#     return cur.fetchone()["id_text"]


# def get_default_category_id(cur) -> int:
#     cur.execute(
#         """
#         INSERT INTO category (name)
#         VALUES ('Не определено')
#         ON CONFLICT (name)
#         DO UPDATE SET name = EXCLUDED.name
#         RETURNING id_categ;
#         """
#     )
#     return cur.fetchone()["id_categ"]


# def resolve_wagon(cur, wagon_value: str):
#     """
#     Принимает номер вагона ИЛИ id_wagon (строкой).
#     Сначала ищем по номеру (как шлёт клиент), затем по id_wagon.
#     Поезд и линия берутся из самого вагона.
#     """

#     wagon_value = (wagon_value or "").strip()
#     if not wagon_value:
#         raise HTTPException(400, "wagon не может быть пустым")

#     # 1) по номеру вагона
#     cur.execute(
#         """
#         SELECT w.id_wagon, w.id_train, t.id_line
#         FROM wagons w
#         JOIN trains t ON t.id_train = w.id_train
#         WHERE w.number = %s
#         ORDER BY w.id_wagon
#         LIMIT 1;
#         """,
#         (wagon_value,),
#     )
#     row = cur.fetchone()

#     if row is None:
#         raise HTTPException(404, f"Вагон не найден: {wagon_value}")

#     return row["id_wagon"], row["id_train"], row["id_line"]


# def resolve_category(cur, category_value: str) -> int:
#     """
#     Принимает id_categ (числом) ИЛИ название категории из /categories.
#     Пустое значение -> дефолтная категория 'Не определено'.
#     """

#     category_value = (category_value or "").strip()
#     if not category_value:
#         return get_default_category_id(cur)

#     if category_value.isdigit():
#         cur.execute(
#             "SELECT id_categ FROM category WHERE id_categ = %s LIMIT 1;",
#             (int(category_value),),
#         )
#         row = cur.fetchone()
#         if row:
#             return row["id_categ"]

#     cur.execute(
#         "SELECT id_categ FROM category WHERE name ILIKE %s LIMIT 1;",
#         (category_value,),
#     )
#     row = cur.fetchone()
#     if row is None:
#         raise HTTPException(404, f"Категория не найдена: {category_value}")

#     return row["id_categ"]


# # ---------- Эндпоинты ----------

# @app.get("/health")
# def health():
#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute("SELECT 1 AS ok;")
#                 row = cur.fetchone()
#         return {"ok": True, "database": row["ok"] == 1}
#     except Exception as e:
#         raise HTTPException(500, f"Backend работает, но БД недоступна: {str(e)}")


# @app.get("/categories", response_model=list[CategoryResponse])
# def get_categories():
#     """
#     Вернуть все категории дефектов. (Оставлено без изменений.)
#     """
#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute("SELECT id_categ, name FROM category ORDER BY id_categ;")
#                 rows = cur.fetchall()
#     except Exception as e:
#         raise HTTPException(500, f"Ошибка при получении категорий: {str(e)}")

#     return rows


# @app.get("/wagons", response_model=list[WagonShortResponse])
# def get_all_wagons():
#     """
#     Список активных вагонов. Нужен клиенту, чтобы выбрать вагон
#     и прислать его id_wagon в /set_foto.
#     """
#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     """
#                     SELECT id_wagon, number
#                     FROM wagons
#                     WHERE activ = true
#                     ORDER BY id_wagon;
#                     """
#                 )
#                 rows = cur.fetchall()
#     except Exception as e:
#         raise HTTPException(500, f"Ошибка при получении вагонов: {str(e)}")

#     return rows


# @app.post("/auth/send-code", response_model=OkResponse)
# def auth_send_code(data: AuthSendRequest):
#     email = normalize_email(str(data.email))
#     code = generate_code()
#     code_hash = hash_value(code)

#     expires_at = datetime.now(timezone.utc) + timedelta(minutes=AUTH_CODE_TTL_MINUTES)

#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     "SELECT ban FROM users WHERE email = %s LIMIT 1;",
#                     (email,),
#                 )
#                 user = cur.fetchone()
#                 if user is not None and user["ban"]:
#                     raise HTTPException(403, "ban")

#                 cur.execute(
#                     """
#                     UPDATE auth_codes
#                     SET used = true
#                     WHERE email = %s AND used = false;
#                     """,
#                     (email,),
#                 )

#                 cur.execute(
#                     """
#                     INSERT INTO auth_codes (email, code_hash, expires_at)
#                     VALUES (%s, %s, %s);
#                     """,
#                     (email, code_hash, expires_at),
#                 )

#             conn.commit()

#         send_auth_code_email(email, code)

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(500, f"Ошибка при отправке кода: {str(e)}")

#     return {"ok": True}


# @app.post("/auth/verify", response_model=AuthVerifyResponse)
# def auth_verify(data: AuthVerifyRequest):
#     email = normalize_email(str(data.email))
#     code = data.code.strip()

#     if not code.isdigit() or len(code) != 6:
#         raise HTTPException(400, "Код должен состоять из 6 цифр")

#     code_hash = hash_value(code)

#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 # создаём/получаем пользователя и проверяем бан
#                 cur.execute(
#                     """
#                     INSERT INTO users (email)
#                     VALUES (%s)
#                     ON CONFLICT (email)
#                     DO UPDATE SET email = EXCLUDED.email
#                     RETURNING id_user, email, ban;
#                     """,
#                     (email,),
#                 )
#                 user = cur.fetchone()

#                 if user["ban"]:
#                     conn.commit()
#                     raise HTTPException(403, "ban")

#                 # берём последний активный код (этого запроса не хватало раньше)
#                 cur.execute(
#                     """
#                     SELECT id_code, code_hash, expires_at, attempts
#                     FROM auth_codes
#                     WHERE email = %s AND used = false
#                     ORDER BY expires_at DESC
#                     LIMIT 1;
#                     """,
#                     (email,),
#                 )
#                 auth_code = cur.fetchone()

#                 if auth_code is None:
#                     raise HTTPException(400, "Активный код не найден")

#                 if auth_code["expires_at"] < datetime.now(timezone.utc):
#                     cur.execute(
#                         "UPDATE auth_codes SET used = true WHERE id_code = %s;",
#                         (auth_code["id_code"],),
#                     )
#                     conn.commit()
#                     raise HTTPException(400, "Код истёк")

#                 if auth_code["attempts"] >= AUTH_MAX_ATTEMPTS:
#                     cur.execute(
#                         "UPDATE auth_codes SET used = true WHERE id_code = %s;",
#                         (auth_code["id_code"],),
#                     )
#                     conn.commit()
#                     raise HTTPException(400, "Превышено количество попыток")

#                 if not secrets.compare_digest(auth_code["code_hash"], code_hash):
#                     cur.execute(
#                         "UPDATE auth_codes SET attempts = attempts + 1 WHERE id_code = %s;",
#                         (auth_code["id_code"],),
#                     )
#                     conn.commit()
#                     raise HTTPException(400, "Неверный код")

#                 # код верный — гасим его
#                 cur.execute(
#                     "UPDATE auth_codes SET used = true WHERE id_code = %s;",
#                     (auth_code["id_code"],),
#                 )

#                 # создаём сессию
#                 token = generate_token()
#                 token_hash = hash_value(token)
#                 session_expires_at = datetime.now(timezone.utc) + timedelta(
#                     days=AUTH_SESSION_TTL_DAYS
#                 )

#                 cur.execute(
#                     """
#                     INSERT INTO auth_sessions (id_user, token_hash, expires_at)
#                     VALUES (%s, %s, %s);
#                     """,
#                     (user["id_user"], token_hash, session_expires_at),
#                 )

#             conn.commit()

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(500, f"Ошибка при проверке кода: {str(e)}")

#     return {"access_token": token, "token_type": "Bearer"}


# @app.get("/auth/me")
# def auth_me(current_user: CurrentUser = Depends(get_current_user)):
#     return {"ok": True, "email": current_user.email}


# @app.post("/set_foto", response_model=OkResponse)
# def set_foto(
#     foto: UploadFile = File(...),
#     wagon: str = Form(...),
#     category: str = Form(...),
#     text_prob: str = Form(""),
#     current_user: CurrentUser = Depends(get_current_user),
# ):
#     """
#     Авторизованный пользователь присылает фото.

#     Поля формы:
#       foto      — файл изображения
#       wagon     — id_wagon (число из /wagons)
#       category  — id_categ (число) или название категории из /categories
#       text_prob — необязательный комментарий

#     Поезд и линия определяются автоматически по выбранному вагону.
#     Фото сохраняется как foto/YYYY-MM-DD/{id}.jpg
#     """

#     today_dir = date.today().isoformat()
#     upload_dir = Path(UPLOAD_ROOT) / today_dir
#     upload_dir.mkdir(parents=True, exist_ok=True)

#     tmp_path = None

#     try:
#         with pool.connection() as conn:
#             with conn.cursor() as cur:
#                 id_wagon, id_train, id_line = resolve_wagon(cur, wagon)
#                 id_categ = resolve_category(cur, category)
#                 id_text = create_text_prob(cur, text_prob)

#                 cur.execute(
#                     """
#                     INSERT INTO defects (
#                         id_wagon,
#                         id_train,
#                         id_text,
#                         id_categ,
#                         id_line
#                     )
#                     VALUES (%s, %s, %s, %s, %s)
#                     RETURNING id;
#                     """,
#                     (id_wagon, id_train, id_text, id_categ, id_line),
#                 )
#                 defect_id = cur.fetchone()["id"]

#                 # пишем во временный файл, переименуем после успешного commit,
#                 # чтобы не оставлять "осиротевший" файл при ошибке БД
#                 file_path = upload_dir / f"{defect_id}.jpg"
#                 tmp_path = upload_dir / f".{defect_id}.tmp"
#                 with open(tmp_path, "wb") as buffer:
#                     shutil.copyfileobj(foto.file, buffer)

#             conn.commit()

#         os.replace(tmp_path, file_path)
#         tmp_path = None

#     except HTTPException:
#         if tmp_path is not None and tmp_path.exists():
#             tmp_path.unlink(missing_ok=True)
#         raise
#     except Exception as e:
#         if tmp_path is not None and tmp_path.exists():
#             tmp_path.unlink(missing_ok=True)
#         raise HTTPException(500, f"Ошибка при сохранении фото: {str(e)}")

#     return {"ok": True}