import os
import json
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

try:
    import psycopg2  # noqa
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nx-dev-" + str(uuid.uuid4()))

raw_url = os.environ.get("DATABASE_URL", "sqlite:///nexarion.db")
if raw_url.startswith("postgres://"):
    raw_url = raw_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = raw_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Fix SQLite concurrent access (crashes with multiple workers)
if raw_url.startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))

CORS(app, supports_credentials=True, origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","))
db = SQLAlchemy(app)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "nexarion2024")


# ── Models ────────────────────────────────────────────────────────────────────
class Setting(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)


class Product(db.Model):
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name        = db.Column(db.String(200), nullable=False)
    category    = db.Column(db.String(100), default="General")
    price       = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, default="")
    image       = db.Column(db.String(20), default="")
    photo       = db.Column(db.Text, default="")        # kept for compat
    photos      = db.Column(db.Text, default="[]")      # JSON array of base64
    stock       = db.Column(db.Integer, default=0)
    tags        = db.Column(db.Text, default="")
    featured    = db.Column(db.Boolean, default=False)
    discount    = db.Column(db.Float, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = db.Column(db.String(200), unique=True, nullable=False)
    name       = db.Column(db.String(200), default="Guest")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cart_items = db.relationship("CartItem", backref="user", lazy=True, cascade="all, delete-orphan")


class CartItem(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.String(36), db.ForeignKey("product.id"), nullable=False)
    quantity   = db.Column(db.Integer, default=1)
    product    = db.relationship("Product")


# ── Default Settings ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "name":     "Organic Food",
    "tagline":  "Where Innovation Meets Commerce",
    "phone":    "+234 800 000 0000",
    "whatsapp": "+2348000000000",
    "address":  "Lagos, Nigeria",
    "email":    "hello@nexarionzone.com",
    "theme":    "#4A7C59",
    "currency": "",
    "logo":           "",
    "placeholder_bg": "#E8F5E9",
    # Colors
    "bg_color":       "#F6F7FB",
    "card_color":     "#FFFFFF",
    # Section visibility (1=show, 0=hide)
    "hero_visible":   "1",
    "search_visible": "1",
    "cats_visible":   "1",
    "footer_visible": "1",
    # Editable text labels
    "hero_eyebrow":   "PREMIUM COLLECTION",
    "btn_wa_text":    "WhatsApp",
    "btn_ph_text":    "Call Us",
    "btn_em_text":    "Email Us",
    "btn_cart_text":  "Add to Cart",
    "btn_view_text":  "View",
}


# ── DB Init ───────────────────────────────────────────────────────────────────
def initialize_db():
    try:
        db.create_all()
    except Exception as e:
        print(f"[init] create_all warning: {e}")

    try:
        from sqlalchemy import text, inspect as sa_inspect
        inspector = sa_inspect(db.engine)
        existing = [c["name"] for c in inspector.get_columns("product")]
        migrations = {
            "tags":     "ALTER TABLE product ADD COLUMN tags TEXT DEFAULT ''",
            "featured": "ALTER TABLE product ADD COLUMN featured BOOLEAN DEFAULT 0",
            "discount": "ALTER TABLE product ADD COLUMN discount FLOAT DEFAULT 0",
            "photo":    "ALTER TABLE product ADD COLUMN photo TEXT DEFAULT ''",
        "photos":   "ALTER TABLE product ADD COLUMN photos TEXT DEFAULT '[]'",
        }
        for col, sql in migrations.items():
            if col not in existing:
                try:
                    db.session.execute(text(sql))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
    except Exception as e:
        print(f"[init] migration warning: {e}")

    try:
        for key, value in DEFAULT_SETTINGS.items():
            if not Setting.query.filter_by(key=key).first():
                db.session.add(Setting(key=key, value=value))
        # No default products — admin adds via /admin
        db.session.commit()
        print("[init] Database ready")
    except Exception as e:
        db.session.rollback()
        print(f"[init] seed warning: {e}")


with app.app_context():
    initialize_db()


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_setting(key):
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else DEFAULT_SETTINGS.get(key, "")


def require_admin():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    return None


def product_dict(p):
    # Build photos array — support both old single photo and new multi-photo
    try:
        photos = json.loads(p.photos or "[]") if p.photos else []
    except Exception:
        photos = []
    if not photos and (p.photo or ""):
        photos = [p.photo]
    return {
        "id":          p.id,
        "name":        p.name,
        "category":    p.category,
        "price":       p.price,
        "description": p.description,
        "image":       p.image or "",
        "photo":       photos[0] if photos else "",
        "photos":      photos,
        "stock":       p.stock,
        "tags":        p.tags or "",
        "featured":    p.featured or False,
        "discount":    p.discount or 0,
    }


def cart_dict(i):
    return {
        "id":         i.id,
        "product_id": i.product_id,
        "name":       i.product.name if i.product else "Deleted",
        "price":      i.product.price if i.product else 0,
        "image":      i.product.photo or "" if i.product else "",
        "quantity":   i.quantity,
    }


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin")
def admin_page():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({k: get_setting(k) for k in DEFAULT_SETTINGS})

@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    err = require_admin()
    if err: return err
    for key, value in (request.json or {}).items():
        if key in DEFAULT_SETTINGS or key in ("logo","bg_color","card_color","hero_visible","search_visible","cats_visible","footer_visible","hero_eyebrow","btn_wa_text","btn_ph_text","btn_em_text","btn_cart_text","btn_view_text","placeholder_bg"):
            s = Setting.query.filter_by(key=key).first()
            if s:
                s.value = value
            else:
                db.session.add(Setting(key=key, value=value))
    db.session.commit()
    return jsonify({"success": True})


# ── Products ──────────────────────────────────────────────────────────────────
@app.route("/api/products", methods=["GET"])
def api_get_products():
    return jsonify([product_dict(p) for p in Product.query.order_by(Product.created_at.asc()).all()])

@app.route("/api/products", methods=["POST"])
def api_add_product():
    err = require_admin()
    if err: return err
    d = request.json or {}
    if not d.get("name") or not d.get("price"):
        return jsonify({"error": "name and price required"}), 400
    p = Product(
        name=d["name"], category=d.get("category", "General"),
        price=float(d["price"]), description=d.get("description", ""),
        image=d.get("image", ""), photo=d.get("photo", ""),
        stock=int(d.get("stock", 0)), tags=d.get("tags", ""),
        featured=bool(d.get("featured", False)),
        discount=float(d.get("discount", 0)),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(product_dict(p)), 201

@app.route("/api/products/<pid>", methods=["PUT"])
def api_update_product(pid):
    err = require_admin()
    if err: return err
    p = db.session.get(Product, pid)
    if not p: return jsonify({"error": "Not found"}), 404
    d = request.json or {}
    p.name        = d.get("name", p.name)
    p.category    = d.get("category", p.category)
    p.price       = float(d.get("price", p.price))
    p.description = d.get("description", p.description)
    p.stock       = int(d.get("stock", p.stock))
    p.tags        = d.get("tags", p.tags or "")
    p.featured    = bool(d.get("featured", p.featured or False))
    p.discount    = float(d.get("discount", p.discount or 0))
    if "photos" in d:
        photos_list = d["photos"] if isinstance(d["photos"], list) else []
        p.photos = json.dumps(photos_list)
        p.photo = photos_list[0] if photos_list else ""
    elif "photo" in d:
        p.photo = d["photo"]
    db.session.commit()
    return jsonify(product_dict(p))

@app.route("/api/products/<pid>", methods=["DELETE"])
def api_delete_product(pid):
    err = require_admin()
    if err: return err
    p = db.session.get(Product, pid)
    if not p: return jsonify({"error": "Not found"}), 404
    CartItem.query.filter_by(product_id=pid).delete()
    db.session.delete(p)
    db.session.commit()
    return jsonify({"success": True})


# ── Users ─────────────────────────────────────────────────────────────────────
@app.route("/api/users", methods=["POST"])
def api_create_user():
    d = request.json or {}
    sid  = d.get("session_id", str(uuid.uuid4()))
    name = d.get("name", "Guest")
    user = User.query.filter_by(session_id=sid).first()
    if not user:
        user = User(session_id=sid, name=name)
        db.session.add(user)
    elif name and name != "Guest":
        user.name = name
    db.session.commit()
    return jsonify({"id": user.id, "name": user.name})


# ── Cart ──────────────────────────────────────────────────────────────────────
@app.route("/api/cart/<user_id>", methods=["GET"])
def api_get_cart(user_id):
    items = CartItem.query.filter_by(user_id=user_id).all()
    return jsonify([cart_dict(i) for i in items if i.product])

@app.route("/api/cart", methods=["POST"])
def api_add_to_cart():
    d = request.json or {}
    user_id, product_id = d.get("user_id"), d.get("product_id")
    if not user_id or not product_id:
        return jsonify({"error": "user_id and product_id required"}), 400
    item = CartItem.query.filter_by(user_id=user_id, product_id=product_id).first()
    if item:
        item.quantity += 1
    else:
        item = CartItem(user_id=user_id, product_id=product_id, quantity=1)
        db.session.add(item)
    db.session.commit()
    db.session.refresh(item)
    return jsonify(cart_dict(item))

@app.route("/api/cart/<int:item_id>", methods=["PUT"])
def api_update_cart(item_id):
    item = db.session.get(CartItem, item_id)
    if not item: return jsonify({"error": "Not found"}), 404
    qty = int((request.json or {}).get("quantity", 1))
    if qty <= 0:
        db.session.delete(item)
    else:
        item.quantity = qty
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/cart/<int:item_id>", methods=["DELETE"])
def api_remove_cart(item_id):
    item = db.session.get(CartItem, item_id)
    if not item: return jsonify({"error": "Not found"}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})


# ── Admin ─────────────────────────────────────────────────────────────────────
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    if (request.json or {}).get("password") == ADMIN_PASSWORD:
        session["admin"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid password"}), 401

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"success": True})

@app.route("/api/admin/check", methods=["GET"])
def api_admin_check():
    return jsonify({"admin": bool(session.get("admin"))})

@app.route("/api/admin/carts", methods=["GET"])
def api_admin_carts():
    err = require_admin()
    if err: return err
    users = User.query.order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        items = [i for i in u.cart_items if i.product]
        if items:
            result.append({
                "user":  {"id": u.id, "name": u.name, "created_at": u.created_at.strftime("%b %d, %Y")},
                "items": [cart_dict(i) for i in items],
                "total": sum(i.product.price * i.quantity for i in items),
            })
    return jsonify(result)

@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    err = require_admin()
    if err: return err
    revenue = db.session.query(
        db.func.sum(CartItem.quantity * Product.price)
    ).join(Product).scalar() or 0
    return jsonify({
        "users":             User.query.count(),
        "products":          Product.query.count(),
        "cart_items":        CartItem.query.count(),
        "potential_revenue": round(revenue, 2),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","0")=="1")
