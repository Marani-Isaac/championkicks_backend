import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "championkicks_backend.env")


def get_connection():
    """Open a fresh MySQL connection using championkicks_backend.env."""
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "imarani_championkicks"),
        port=int(os.getenv("DB_PORT", "3306")),
        cursorclass=pymysql.cursors.DictCursor,
    )
