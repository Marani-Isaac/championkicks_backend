# ChampionKicks Flask + PyMySQL API

Simple Flask routes with direct PyMySQL queries against AlwaysData
(`imarani_championkicks`). Credentials live in `championkicks_backend.env`
(never commit that file).

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## Endpoints

All mutating routes expect `multipart/form-data` (`request.form` / `request.files`).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/signup` | Register user |
| POST | `/api/login` | Login |
| POST | `/api/add_product` | Add product (+ optional image file) |
| GET | `/api/get_products` | List products |
| GET | `/api/get_product/<id>` | Product by id |
| POST | `/api/add_order` | Create order |
| GET | `/api/get_orders` | List orders |
| POST | `/api/add_payment` | Record payment |
| GET | `/api/get_payments` | List payments (`?username=`) |
| POST | `/api/add_testimonial` | Add review |
| GET | `/api/get_testimonials` | List reviews (`?approved=false` for all) |
| POST | `/api/mpesa_payment` | Daraja STK push (`amount`, `phone`) |
| GET | `/health` | Health check |

Product images are saved under `static/images/`.
