import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "championkicks_backend.env", override=True)

_schema_ready = False


def _add_column(cursor, table, column, definition):
    try:
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
    except (pymysql.err.OperationalError, pymysql.err.ProgrammingError) as error:
        # 1060 = Duplicate column name
        if error.args and error.args[0] == 1060:
            return
        raise


def ensure_schema():
    """Add order/product/payment columns used by the admin workflow."""
    global _schema_ready
    if _schema_ready:
        return

    connection = get_connection()
    try:
        cursor = connection.cursor()
        _add_column(cursor, "products", "commission_percent", "DECIMAL(10,2) NOT NULL DEFAULT 0")
        _add_column(cursor, "products", "commission_min_qty", "INT NOT NULL DEFAULT 2")
        _add_column(cursor, "products", "available", "TINYINT(1) NOT NULL DEFAULT 1")

        _add_column(cursor, "order", "user_id", "INT NULL")
        _add_column(cursor, "order", "username", "VARCHAR(191) NULL")
        _add_column(cursor, "order", "email", "VARCHAR(191) NULL")
        _add_column(cursor, "order", "phone", "VARCHAR(64) NULL")
        _add_column(cursor, "order", "address", "VARCHAR(255) NULL")
        _add_column(cursor, "order", "status", "VARCHAR(32) NOT NULL DEFAULT 'pending'")
        _add_column(cursor, "order", "delivery_cost", "DECIMAL(10,2) NOT NULL DEFAULT 0")
        _add_column(cursor, "order", "total_amount", "DECIMAL(10,2) NOT NULL DEFAULT 0")
        _add_column(cursor, "order", "pack_id", "INT NULL")

        _add_column(cursor, "payments", "order_id", "INT NULL")
        try:
            cursor.execute("UPDATE `order` SET pack_id = order_id WHERE pack_id IS NULL")
        except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
            pass
        # AlwaysData testimonial_id has no AUTO_INCREMENT; a leftover 0 PK
        # makes later inserts fail with Duplicate entry '0'.
        try:
            cursor.execute(
                "SELECT testimonial_id FROM testimonial WHERE testimonial_id = 1"
            )
            if not cursor.fetchone():
                cursor.execute(
                    "UPDATE testimonial SET testimonial_id = 1 WHERE testimonial_id = 0"
                )
        except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
            pass
        connection.commit()
        _schema_ready = True
    finally:
        connection.close()


def get_connection():
    """Open a fresh MySQL connection using championkicks_backend.env."""
    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "imarani_championkicks")
    port = int(os.getenv("DB_PORT", "3306"))

    if not host or not user:
        raise pymysql.err.OperationalError(
            2003,
            "Database credentials missing. Set DB_HOST, DB_USER, DB_PASSWORD, "
            "and DB_NAME in championkicks_backend.env",
        )

    return pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=port,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20,
    )
