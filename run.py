import base64
import datetime
import json
import os
from pathlib import Path

import pymysql
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from requests.auth import HTTPBasicAuth

from db import ensure_schema, get_connection

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "championkicks_backend.env", override=True)

app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=False,
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)

UPLOAD_FOLDER = BASE_DIR / "static" / "images"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)


@app.errorhandler(pymysql.MySQLError)
def handle_mysql_error(error):
    return jsonify({"message": "database error", "error": str(error)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "championkicks_backend"})


# -------------------- AUTH --------------------

@app.route("/api/signup", methods=["POST"])
def signup():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    address = request.form.get("address", "").strip()

    if not username or not email or not password:
        return jsonify({"message": "username, email and password are required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if cursor.rowcount > 0:
            return jsonify({"message": "user with this email already exists"}), 400

        # Some AlwaysData schemas have user_id without AUTO_INCREMENT.
        cursor.execute("SELECT COALESCE(MAX(user_id), 0) + 1 AS next_id FROM users")
        next_id = cursor.fetchone()["next_id"]

        sql = (
            "INSERT INTO users (user_id, username, email, phone, password, address) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        )
        data = (next_id, username, email, phone, password, address)
        cursor.execute(sql, data)
        connection.commit()

        return jsonify({"message": "Sign up successful", "user_id": next_id}), 201
    finally:
        connection.close()


@app.route("/api/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return jsonify({"message": "email and password are required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        sql = (
            "SELECT user_id, username, email, phone, address "
            "FROM users WHERE email = %s AND password = %s"
        )
        cursor.execute(sql, (email, password))

        if cursor.rowcount == 0:
            return jsonify({"message": "invalid credentials"}), 401

        user = cursor.fetchone()
        return jsonify({"message": "login successful", "user": user}), 200
    finally:
        connection.close()


def _parse_available(value, default=1):
    if isinstance(value, (bytes, bytearray)):
        value = value[0] if value else 0
    if value is None or value == "":
        return default
    if value is True or value == 1:
        return 1
    if value is False or value == 0:
        return 0
    text = str(value).strip().lower()
    if text in ("1", "true", "available", "yes", "in_stock"):
        return 1
    if text in ("0", "false", "unavailable", "no", "out_of_stock", "0.0", "0.00"):
        return 0
    try:
        return 1 if float(text) != 0 else 0
    except (TypeError, ValueError):
        return default


def _serialize_product(row):
    if not row:
        return row
    product = dict(row)
    product["available"] = _parse_available(product.get("available"), 1)
    product["commission_percent"] = _to_number(product.get("commission_percent"), 0)
    product["commission_min_qty"] = int(_to_number(product.get("commission_min_qty"), 2) or 2)
    product["product_cost"] = _to_number(product.get("product_cost"), 0)
    return product


def _line_pricing(product, quantity):
    unit = _to_number((product or {}).get("product_cost"), 0)
    qty = max(int(_to_number(quantity, 1)), 1)
    percent = max(_to_number((product or {}).get("commission_percent"), 0), 0)
    min_qty = max(int(_to_number((product or {}).get("commission_min_qty"), 2) or 2), 1)
    gross = unit * qty
    extra_qty = qty - (min_qty - 1) if qty >= min_qty else 0
    extra_qty = max(extra_qty, 0)
    discount = (unit * extra_qty * percent / 100.0) if extra_qty and percent else 0.0
    return {
        "unit": unit,
        "quantity": qty,
        "gross": gross,
        "discount": discount,
        "net": max(0.0, gross - discount),
    }


def _to_number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _next_table_id(cursor, table, column):
    cursor.execute(
        f"SELECT COALESCE(MAX(`{column}`), 0) AS max_id FROM `{table}`"
    )
    row = cursor.fetchone() or {}
    max_id = int(row.get("max_id") or 0)
    next_id = max_id + 1
    return next_id if next_id > 0 else 1


def _next_unique_id(cursor, table, column):
    next_id = _next_table_id(cursor, table, column)
    if next_id <= 0:
        next_id = 1
    cursor.execute(
        f"SELECT `{column}` FROM `{table}` WHERE `{column}` = %s",
        (next_id,),
    )
    while cursor.fetchone():
        next_id += 1
        cursor.execute(
            f"SELECT `{column}` FROM `{table}` WHERE `{column}` = %s",
            (next_id,),
        )
    return next_id


def _next_order_id(cursor):
    """AlwaysData order_id is a PK without AUTO_INCREMENT; 0 may already exist."""
    return _next_unique_id(cursor, "order", "order_id")


def _optional_int(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merge_order_customer(row):
    return {
        **row,
        "username": row.get("order_username") or row.get("user_username") or row.get("username"),
        "email": row.get("order_email") or row.get("user_email") or row.get("email"),
        "phone": row.get("order_phone") or row.get("user_phone") or row.get("phone"),
        "address": row.get("order_address") or row.get("user_address") or row.get("address"),
        "status": (row.get("status") or "pending"),
        "delivery_cost": _to_number(row.get("delivery_cost"), 0),
        "total_amount": _to_number(row.get("total_amount"), 0),
        "pack_id": row.get("pack_id") or row.get("order_id"),
    }


def _parse_order_items():
    raw = (request.form.get("items") or "").strip()
    items = []
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, dict):
            parsed = [parsed]
        for row in parsed or []:
            if not isinstance(row, dict):
                continue
            pid = row.get("product_id") or row.get("id")
            if not pid:
                continue
            items.append(
                {
                    "product_id": pid,
                    "quantity": row.get("quantity", 1),
                }
            )
    if not items:
        pid = request.form.get("product_id")
        if pid:
            items.append(
                {
                    "product_id": pid,
                    "quantity": request.form.get("quantity", "1"),
                }
            )
    return items


def _insert_order_line(cursor, payload):
    order_id = _next_order_id(cursor)
    sql = (
        "INSERT INTO `order` "
        "(order_id, pack_id, product_id, quantity, user_id, username, email, phone, address, "
        "status, delivery_cost, total_amount) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    values = (
        order_id,
        payload["pack_id"],
        payload["product_id"],
        str(payload["quantity"]),
        payload["user_id"],
        payload["username"],
        payload["email"],
        payload["phone"],
        payload["address"],
        "pending",
        0,
        payload["line_total"],
    )
    for _ in range(8):
        try:
            cursor.execute(sql, values)
            return order_id
        except pymysql.err.IntegrityError as err:
            if not err.args or err.args[0] != 1062:
                raise
            order_id = _next_order_id(cursor)
            values = (order_id,) + values[1:]
    raise RuntimeError("Could not allocate a new order id")


# -------------------- PRODUCTS --------------------

@app.route("/api/add_product", methods=["POST"])
def add_product():
    ensure_schema()
    product_name = request.form.get("product_name", "").strip()
    product_description = request.form.get("product_description", "").strip()
    product_category = request.form.get("product_category", "").strip()
    product_cost = request.form.get("product_cost", "").strip()
    commission_percent = request.form.get("commission_percent", "0").strip() or "0"
    commission_min_qty = request.form.get("commission_min_qty", "2").strip() or "2"
    available = _parse_available(request.form.get("available"), 1)
    product_image = request.files.get("product_image")

    if not product_name or not product_cost:
        return jsonify({"message": "product_name and product_cost are required"}), 400

    image_name = ""
    if product_image and product_image.filename:
        image_name = product_image.filename
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], image_name)
        product_image.save(file_path)

    connection = get_connection()
    try:
        cursor = connection.cursor()

        # Some AlwaysData schemas have product_id without AUTO_INCREMENT.
        cursor.execute(
            "SELECT COALESCE(MAX(product_id), 0) + 1 AS next_id FROM products"
        )
        next_id = cursor.fetchone()["next_id"]

        sql = (
            "INSERT INTO products "
            "(product_id, product_name, product_description, product_category, "
            "product_cost, product_image, commission_percent, commission_min_qty, available) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        data = (
            next_id,
            product_name,
            product_description,
            product_category,
            product_cost,
            image_name,
            commission_percent,
            commission_min_qty,
            available,
        )
        cursor.execute(sql, data)
        connection.commit()
        return jsonify(
            {"message": "Product added successfully", "product_id": next_id}
        ), 201
    finally:
        connection.close()


@app.route("/api/update_product", methods=["POST"])
def update_product():
    ensure_schema()
    product_id = request.form.get("product_id")
    if not product_id:
        return jsonify({"message": "product_id is required"}), 400

    fields = []
    values = []
    if "available" in request.form:
        fields.append("available = %s")
        values.append(_parse_available(request.form.get("available")))
    if "commission_percent" in request.form:
        fields.append("commission_percent = %s")
        values.append(request.form.get("commission_percent") or "0")
    if "commission_min_qty" in request.form:
        fields.append("commission_min_qty = %s")
        values.append(request.form.get("commission_min_qty") or "2")

    if not fields:
        return jsonify({"message": "no fields to update"}), 400

    try:
        values.append(int(product_id))
    except (TypeError, ValueError):
        values.append(product_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"UPDATE products SET {', '.join(fields)} WHERE product_id = %s",
            values,
        )
        connection.commit()
        if cursor.rowcount == 0:
            return jsonify({"message": "product not found"}), 404
        return jsonify({"message": "Product updated successfully", "available": request.form.get("available")}), 200
    finally:
        connection.close()


@app.route("/api/get_products", methods=["GET"])
def get_products():
    ensure_schema()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM products ORDER BY product_id DESC")

        if cursor.rowcount == 0:
            return jsonify({"message": "no products found", "products": []}), 200

        products = [_serialize_product(row) for row in cursor.fetchall()]
        return jsonify({"products": products}), 200
    finally:
        connection.close()


@app.route("/api/get_product/<int:product_id>", methods=["GET"])
def get_product(product_id):
    ensure_schema()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT * FROM products WHERE product_id = %s",
            (product_id,),
        )

        if cursor.rowcount == 0:
            return jsonify({"message": "product not found"}), 404

        product = _serialize_product(cursor.fetchone())
        return jsonify({"product": product}), 200
    finally:
        connection.close()


# -------------------- ORDERS --------------------

@app.route("/api/add_order", methods=["POST"])
def add_order():
    ensure_schema()
    items = _parse_order_items()
    user_id = _optional_int(request.form.get("user_id"))
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()[:255]
    notes = request.form.get("notes", "").strip()
    if notes:
        extra = f" | Notes: {notes}"
        address = (address + extra)[:255]

    if not items:
        return jsonify({"message": "product_id or items are required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()

        if user_id:
            cursor.execute(
                "SELECT username, email, phone, address FROM users WHERE user_id = %s",
                (user_id,),
            )
            user = cursor.fetchone()
            if user:
                username = username or (user.get("username") or "")
                email = email or (user.get("email") or "")
                phone = phone or (user.get("phone") or "")
                address = address or (user.get("address") or "")

        lines = []
        pack_total = 0
        for item in items:
            product_id = item["product_id"]
            qty = max(int(_to_number(item.get("quantity"), 1)), 1)
            cursor.execute(
                "SELECT product_id, product_cost, product_name, commission_percent, "
                "commission_min_qty, available FROM products WHERE product_id = %s",
                (product_id,),
            )
            product = cursor.fetchone()
            if not product:
                return jsonify({"message": f"product not found: {product_id}"}), 404
            if _parse_available(product.get("available"), 1) != 1:
                return jsonify(
                    {
                        "message": f"{product.get('product_name') or 'Product'} is unavailable"
                    }
                ), 400
            pricing = _line_pricing(product, qty)
            pack_total += pricing["net"]
            lines.append(
                {
                    "product_id": product_id,
                    "quantity": qty,
                    "line_total": pricing["net"],
                    "discount": pricing["discount"],
                    "product_name": product.get("product_name"),
                }
            )

        pack_id = _next_order_id(cursor)
        created_ids = []
        for line in lines:
            order_id = _insert_order_line(
                cursor,
                {
                    "pack_id": pack_id,
                    "product_id": line["product_id"],
                    "quantity": line["quantity"],
                    "user_id": user_id,
                    "username": username,
                    "email": email,
                    "phone": phone,
                    "address": address,
                    "line_total": line["line_total"],
                },
            )
            created_ids.append(order_id)

        connection.commit()
        return jsonify(
            {
                "message": "Order pack added successfully",
                "order_id": pack_id,
                "pack_id": pack_id,
                "order_ids": created_ids,
                "item_count": len(created_ids),
                "pack_total": pack_total,
            }
        ), 201
    except RuntimeError as err:
        return jsonify({"message": str(err)}), 500
    finally:
        connection.close()


@app.route("/api/get_orders", methods=["GET"])
def get_orders():
    ensure_schema()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        sql = (
            "SELECT o.order_id, o.pack_id, o.product_id, o.quantity, o.order_date, "
            "o.user_id, o.username AS order_username, o.email AS order_email, "
            "o.phone AS order_phone, o.address AS order_address, "
            "o.status, o.delivery_cost, o.total_amount, "
            "p.product_name, p.product_cost, p.product_image, "
            "p.commission_percent, p.commission_min_qty, "
            "u.username AS user_username, u.email AS user_email, "
            "u.phone AS user_phone, u.address AS user_address "
            "FROM `order` o "
            "LEFT JOIN products p ON o.product_id = p.product_id "
            "LEFT JOIN users u ON o.user_id = u.user_id "
            "ORDER BY o.order_date DESC"
        )
        cursor.execute(sql)

        if cursor.rowcount == 0:
            return jsonify({"message": "no orders found", "orders": [], "order_packs": []}), 200

        orders = [_merge_order_customer(row) for row in cursor.fetchall()]
        packs = {}
        for row in orders:
            key = row.get("pack_id") or row.get("order_id")
            if key not in packs:
                packs[key] = {
                    "pack_id": key,
                    "order_id": key,
                    "username": row.get("username"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "address": row.get("address"),
                    "user_id": row.get("user_id"),
                    "status": row.get("status") or "pending",
                    "delivery_cost": _to_number(row.get("delivery_cost"), 0),
                    "order_date": row.get("order_date"),
                    "items": [],
                    "subtotal": 0,
                    "discount_total": 0,
                    "total_amount": 0,
                }
            pack = packs[key]
            pricing = _line_pricing(row, row.get("quantity"))
            stored = _to_number(row.get("total_amount"), 0)
            net = stored if stored > 0 else pricing["net"]
            pack["items"].append(
                {
                    **row,
                    "unit_price": pricing["unit"],
                    "gross": pricing["gross"],
                    "discount": pricing["discount"] if stored <= 0 else max(0, pricing["gross"] - net),
                    "line_total": net,
                }
            )
            pack["subtotal"] += net
            pack["discount_total"] += pricing["discount"] if stored <= 0 else max(0, pricing["gross"] - net)
            pack["delivery_cost"] = _to_number(row.get("delivery_cost"), pack["delivery_cost"])
            pack["status"] = row.get("status") or pack["status"]
            pack["total_amount"] = pack["subtotal"] + pack["delivery_cost"]
        return jsonify({"orders": orders, "order_packs": list(packs.values())}), 200
    finally:
        connection.close()


@app.route("/api/update_order", methods=["POST"])
def update_order():
    ensure_schema()
    order_id = request.form.get("order_id")
    if not order_id:
        return jsonify({"message": "order_id is required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT o.order_id, o.pack_id, o.product_id, o.quantity, o.username, o.email, "
            "o.status, o.delivery_cost, p.product_cost, p.commission_percent, p.commission_min_qty "
            "FROM `order` o "
            "LEFT JOIN products p ON o.product_id = p.product_id "
            "WHERE o.order_id = %s",
            (order_id,),
        )
        if cursor.rowcount == 0:
            return jsonify({"message": "order not found"}), 404
        order = cursor.fetchone()
        pack_id = order.get("pack_id") or order.get("order_id")

        cursor.execute(
            "SELECT o.order_id, o.product_id, o.quantity, p.product_cost, "
            "p.commission_percent, p.commission_min_qty "
            "FROM `order` o "
            "LEFT JOIN products p ON o.product_id = p.product_id "
            "WHERE o.pack_id = %s OR o.order_id = %s",
            (pack_id, pack_id),
        )
        pack_lines = cursor.fetchall() or [order]

        delivery_cost = (
            _to_number(request.form.get("delivery_cost"), _to_number(order.get("delivery_cost"), 0))
            if "delivery_cost" in request.form
            else _to_number(order.get("delivery_cost"), 0)
        )
        status = (request.form.get("status") or order.get("status") or "pending").strip().lower()
        if status in ("complete", "completed", "paid"):
            status = "complete"
        else:
            status = "pending"

        pack_subtotal = 0
        for line in pack_lines:
            pricing = _line_pricing(line, line.get("quantity"))
            pack_subtotal += pricing["net"]
        pack_total = pack_subtotal + delivery_cost

        cursor.execute(
            "UPDATE `order` SET delivery_cost = %s, status = %s WHERE pack_id = %s OR order_id = %s",
            (delivery_cost, status, pack_id, pack_id),
        )
        for line in pack_lines:
            pricing = _line_pricing(line, line.get("quantity"))
            cursor.execute(
                "UPDATE `order` SET total_amount = %s WHERE order_id = %s",
                (pricing["net"], line.get("order_id")),
            )

        if status == "complete":
            cursor.execute(
                "SELECT payment_id FROM payments WHERE order_id = %s",
                (pack_id,),
            )
            if cursor.rowcount == 0:
                username = (
                    order.get("username")
                    or request.form.get("username", "").strip()
                    or order.get("email")
                    or "customer"
                )
                payment_id = _next_unique_id(cursor, "payments", "payment_id")
                cursor.execute(
                    "INSERT INTO payments "
                    "(payment_id, username, amount, product_id, payment_status, order_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        payment_id,
                        username,
                        pack_total,
                        order.get("product_id"),
                        "completed",
                        pack_id,
                    ),
                )

        connection.commit()
        return jsonify(
            {
                "message": "Order updated successfully",
                "status": status,
                "delivery_cost": delivery_cost,
                "total_amount": pack_total,
                "pack_id": pack_id,
            }
        ), 200
    finally:
        connection.close()


# -------------------- PAYMENTS --------------------

@app.route("/api/add_payment", methods=["POST"])
def add_payment():
    username = request.form.get("username", "").strip()
    amount = request.form.get("amount")
    product_id = request.form.get("product_id")
    payment_status = request.form.get("payment_status", "pending").strip()
    order_id = _optional_int(request.form.get("order_id"))

    if not username or amount is None or not product_id:
        return jsonify(
            {"message": "username, amount and product_id are required"}
        ), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        # AlwaysData `payments.payment_id` is a PK without AUTO_INCREMENT.
        payment_id = _next_unique_id(cursor, "payments", "payment_id")
        sql = (
            "INSERT INTO payments "
            "(payment_id, username, amount, product_id, payment_status, order_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        )
        values = (
            payment_id,
            username,
            amount,
            product_id,
            payment_status,
            order_id,
        )
        for _ in range(8):
            try:
                cursor.execute(sql, values)
                break
            except pymysql.err.IntegrityError as err:
                if not err.args or err.args[0] != 1062:
                    raise
                payment_id = _next_unique_id(cursor, "payments", "payment_id")
                values = (payment_id,) + values[1:]
        else:
            return jsonify({"message": "Could not allocate a new payment id"}), 500

        connection.commit()
        return jsonify(
            {"message": "Payment recorded successfully", "payment_id": payment_id}
        ), 201
    finally:
        connection.close()


@app.route("/api/get_payments", methods=["GET"])
def get_payments():
    username = request.args.get("username")

    connection = get_connection()
    try:
        cursor = connection.cursor()
        if username:
            cursor.execute(
                "SELECT * FROM payments WHERE username = %s "
                "ORDER BY payment_date DESC",
                (username,),
            )
        else:
            cursor.execute("SELECT * FROM payments ORDER BY payment_date DESC")

        if cursor.rowcount == 0:
            return jsonify({"message": "no payments found", "payments": []}), 200

        payments = cursor.fetchall()
        return jsonify({"payments": payments}), 200
    finally:
        connection.close()


# -------------------- TESTIMONIALS --------------------

def _next_testimonial_id(cursor):
    """AlwaysData testimonial_id is a PK without AUTO_INCREMENT; 0 may already exist."""
    return _next_unique_id(cursor, "testimonial", "testimonial_id")


@app.route("/api/add_testimonial", methods=["POST"])
def add_testimonial():
    username = request.form.get("username", "").strip()
    review = request.form.get("review", "").strip()
    rating = request.form.get("rating", "").strip()
    approved = _parse_available(request.form.get("approved", "0"), 0)

    if not username or not review or not rating:
        return jsonify({"message": "username, review and rating are required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        testimonial_id = _next_testimonial_id(cursor)
        sql = (
            "INSERT INTO testimonial "
            "(testimonial_id, username, review, rating, approved) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        cursor.execute(sql, (testimonial_id, username, review, rating, approved))
        connection.commit()
        return jsonify(
            {
                "message": "Testimonial added successfully",
                "testimonial_id": testimonial_id,
            }
        ), 201
    finally:
        connection.close()


@app.route("/api/update_testimonial", methods=["POST"])
def update_testimonial():
    testimonial_id = _optional_int(request.form.get("testimonial_id"))
    if testimonial_id is None:
        return jsonify({"message": "testimonial_id is required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT * FROM testimonial WHERE testimonial_id = %s",
            (testimonial_id,),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "testimonial not found"}), 404

        approved = row.get("approved")
        if "approved" in request.form:
            approved = _parse_available(request.form.get("approved"), 0)

        review = request.form.get("review", row.get("review"))
        rating = request.form.get("rating", row.get("rating"))

        cursor.execute(
            "UPDATE testimonial SET review = %s, rating = %s, approved = %s "
            "WHERE testimonial_id = %s",
            (review, rating, approved, testimonial_id),
        )
        connection.commit()
        return jsonify({"message": "Testimonial updated successfully"}), 200
    finally:
        connection.close()


@app.route("/api/get_testimonials", methods=["GET"])
def get_testimonials():
    approved_only = request.args.get("approved", "true").lower() != "false"

    connection = get_connection()
    try:
        cursor = connection.cursor()
        if approved_only:
            cursor.execute(
                "SELECT * FROM testimonial WHERE approved = 1 "
                "ORDER BY testimonial_id DESC"
            )
        else:
            cursor.execute(
                "SELECT * FROM testimonial ORDER BY testimonial_id DESC"
            )

        testimonials = cursor.fetchall() or []
        if not testimonials:
            return jsonify(
                {"message": "no testimonials found", "testimonials": []}
            ), 200

        return jsonify({"testimonials": testimonials}), 200
    finally:
        connection.close()


# -------------------- MPESA STK PUSH --------------------

@app.route("/api/mpesa_payment", methods=["POST"])
def mpesa_payment():
    amount = request.form.get("amount", "1")
    phone = request.form.get("phone", "").strip()

    if not phone:
        return jsonify({"message": "phone is required"}), 400

    consumer_key = os.getenv("MPESA_CONSUMER_KEY", "")
    consumer_secret = os.getenv("MPESA_CONSUMER_SECRET", "")
    passkey = os.getenv("MPESA_PASSKEY", "")
    shortcode = os.getenv("MPESA_SHORTCODE", "174379")
    callback_url = os.getenv(
        "MPESA_CALLBACK_URL",
        "https://modcom.co.ke/api/confirmation.php",
    )

    if not consumer_key or not consumer_secret or not passkey:
        return jsonify({"message": "M-Pesa credentials are not configured"}), 500

    # Access token
    auth_url = (
        "https://sandbox.safaricom.co.ke/oauth/v1/generate"
        "?grant_type=client_credentials"
    )
    auth_response = requests.get(
        auth_url,
        auth=HTTPBasicAuth(consumer_key, consumer_secret),
        timeout=30,
    )
    access_token = "Bearer " + auth_response.json()["access_token"]

    # Password
    timestamp = datetime.datetime.today().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        (shortcode + passkey + timestamp).encode()
    ).decode("utf-8")

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": "ChampionKicks",
        "TransactionDesc": "ChampionKicks Payment",
    }

    headers = {
        "Authorization": access_token,
        "Content-Type": "application/json",
    }
    stk_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    response = requests.post(stk_url, json=payload, headers=headers, timeout=30)

    return jsonify(
        {
            "message": "Please complete payment on your phone",
            "mpesa_response": response.json() if response.content else {},
        }
    ), 200


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=debug)
