"""
Назначить пользователя администратором (idempotent).

Использование:
    python superuser.py                      # admin@example.com
    python superuser.py user@mail.ru         # любой email из аргумента
    ADMIN_EMAIL=user@mail.ru python superuser.py
"""
import os
import sys

from sqlalchemy import text

from database import SessionLocal


def make_admin(email: str) -> None:
    email = email.strip().lower()
    if not email:
        raise SystemExit("Не указан email")

    with SessionLocal() as db:
        # ON CONFLICT — чтобы скрипт можно было запускать повторно
        # и чтобы повышать до админа уже существующего пользователя.
        db.execute(
            text("""
                INSERT INTO users (email, admin)
                VALUES (:email, true)
                ON CONFLICT (email) DO UPDATE SET admin = true
            """),
            {"email": email},
        )
        db.commit()

    print(f"Admin ready: {email}")


if __name__ == "__main__":
    target = (
        sys.argv[1] if len(sys.argv) > 1
        else os.getenv("ADMIN_EMAIL", "admin@example.com")
    )
    make_admin(target)