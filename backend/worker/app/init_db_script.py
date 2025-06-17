# /backend/worker/app/init_db_script.py
from .db.session import init_db

print("Initializing database...")
init_db()
print("Database initialized.")