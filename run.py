import base64
import datetime
import os
from pathlib import Path

import pymysql
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from requests.auth import HTTPBasicAuth

from db import get_connection

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "championkicks_backend.env")

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


# -------------------- PRODUCTS --------------------

@app.route("/api/add_product", methods=["POST"])
def add_product():
    product_name = request.form.get("product_name", "").strip()
    product_description = request.form.get("product_description", "").strip()
    product_category = request.form.get("product_category", "").strip()
    product_cost = request.form.get("product_cost", "").strip()
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
            "product_cost, product_image) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        )
        data = (
            next_id,
            product_name,
            product_description,
            product_category,
            product_cost,
            image_name,
        )
        cursor.execute(sql, data)
        connection.commit()
        return jsonify(
            {"message": "Product added successfully", "product_id": next_id}
        ), 201
    finally:
        connection.close()


@app.route("/api/get_products", methods=["GET"])
def get_products():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM products ORDER BY product_id DESC")

        if cursor.rowcount == 0:
            return jsonify({"message": "no products found", "products": []}), 200

        products = cursor.fetchall()
        return jsonify({"products": products}), 200
    finally:
        connection.close()


@app.route("/api/get_product/<int:product_id>", methods=["GET"])
def get_product(product_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT * FROM products WHERE product_id = %s",
            (product_id,),
        )

        if cursor.rowcount == 0:
            return jsonify({"message": "product not found"}), 404

        product = cursor.fetchone()
        return jsonify({"product": product}), 200
    finally:
        connection.close()


# -------------------- ORDERS --------------------

@app.route("/api/add_order", methods=["POST"])
def add_order():
    product_id = request.form.get("product_id")
    quantity = request.form.get("quantity", "1")

    if not product_id:
        return jsonify({"message": "product_id is required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            "SELECT product_id FROM products WHERE product_id = %s",
            (product_id,),
        )
        if cursor.rowcount == 0:
            return jsonify({"message": "product not found"}), 404

        sql = "INSERT INTO `order` (product_id, quantity) VALUES (%s, %s)"
        cursor.execute(sql, (product_id, str(quantity)))
        connection.commit()

        return jsonify({"message": "Order added successfully"}), 201
    finally:
        connection.close()


@app.route("/api/get_orders", methods=["GET"])
def get_orders():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        sql = (
            "SELECT o.order_id, o.product_id, o.quantity, o.order_date, "
            "p.product_name, p.product_cost, p.product_image "
            "FROM `order` o "
            "LEFT JOIN products p ON o.product_id = p.product_id "
            "ORDER BY o.order_date DESC"
        )
        cursor.execute(sql)

        if cursor.rowcount == 0:
            return jsonify({"message": "no orders found", "orders": []}), 200

        orders = cursor.fetchall()
        return jsonify({"orders": orders}), 200
    finally:
        connection.close()


# -------------------- PAYMENTS --------------------

@app.route("/api/add_payment", methods=["POST"])
def add_payment():
    username = request.form.get("username", "").strip()
    amount = request.form.get("amount")
    product_id = request.form.get("product_id")
    payment_status = request.form.get("payment_status", "pending").strip()

    if not username or amount is None or not product_id:
        return jsonify(
            {"message": "username, amount and product_id are required"}
        ), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        sql = (
            "INSERT INTO payments "
            "(username, amount, product_id, payment_status) "
            "VALUES (%s, %s, %s, %s)"
        )
        cursor.execute(sql, (username, amount, product_id, payment_status))
        connection.commit()
        return jsonify({"message": "Payment recorded successfully"}), 201
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

@app.route("/api/add_testimonial", methods=["POST"])
def add_testimonial():
    username = request.form.get("username", "").strip()
    review = request.form.get("review", "").strip()
    rating = request.form.get("rating", "").strip()
    approved = request.form.get("approved", "0")

    if not username or not review or not rating:
        return jsonify({"message": "username, review and rating are required"}), 400

    connection = get_connection()
    try:
        cursor = connection.cursor()
        sql = (
            "INSERT INTO testimonial (username, review, rating, approved) "
            "VALUES (%s, %s, %s, %s)"
        )
        cursor.execute(sql, (username, review, rating, int(approved)))
        connection.commit()
        return jsonify({"message": "Testimonial added successfully"}), 201
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

        if cursor.rowcount == 0:
            return jsonify(
                {"message": "no testimonials found", "testimonials": []}
            ), 200

        testimonials = cursor.fetchall()
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
