from database import get_db
from sqlalchemy import text

db = next(get_db())

db.execute(text("""
    INSERT INTO users (email, admin)
    VALUES (:email, true)
"""), {"email": "admin@example.com"})

db.commit()

print("Admin created")