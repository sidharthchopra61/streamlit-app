"""
STOCKLABEL AI - Multi-Tenant Enterprise SaaS Business Suite & Public Portal
Products • Inventory • Purchase • Sales • Payments • Creditors & Debtors • Barcodes • Labels • Reports • Ledgers • Multi-Tenancy
Database: PostgreSQL / SQLite | Interface: Streamlit
"""

# =====================================================================
# 1. IMPORTS & DEPENDENCIES (NO EXTERNAL BCRYPT REQUIRED)
# =====================================================================
import os
import io
import re
import sys
import json
import sqlite3
import hashlib
import datetime
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import numpy as np
import streamlit as st

# Imaging and Barcode/QR Generation
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode import Code128, EAN13, EAN8, Code39, UPCA
from barcode.writer import ImageWriter
import qrcode

# PDF Engine
from reportlab.lib.pagesizes import letter, A4, A5
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm

# Plotting
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# Optional PostgreSQL Driver (Falls back to SQLite if psycopg2 is missing or DATABASE_URL is unset)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# =====================================================================
# 2. CONFIGURATION & SESSION STATE
# =====================================================================
st.set_page_config(
    page_title="StockLabel AI | Universal Enterprise Business Suite",
    page_icon="🏷️",
    layout="wide",
    initial_sidebar_state="expanded"
)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///stocklabel_saas.db")
SQLITE_DB_PATH = "stocklabel_saas.db"

# Session State Keys
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "tenant_id" not in st.session_state:
    st.session_state.tenant_id = None
if "user_role" not in st.session_state:
    st.session_state.user_role = "OWNER"
if "current_page" not in st.session_state:
    st.session_state.current_page = "🏠 Dashboard"
if "purchase_items" not in st.session_state:
    st.session_state.purchase_items = []
if "sales_items" not in st.session_state:
    st.session_state.sales_items = []
if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "LANDING"


def navigate_to(page_name: str, **kwargs):
    st.session_state.current_page = page_name
    for key, val in kwargs.items():
        st.session_state[key] = val
    st.rerun()


# =====================================================================
# 3. GLOBAL UNIFIED NOTIFICATION ENGINE
# =====================================================================
def notify(message: str, n_type: str = "success"):
    """Displays a consistent non-blocking toast notification based on type."""
    if n_type == "success":
        st.toast(f"✓ {message}", icon="✅")
    elif n_type == "error":
        st.toast(f"✕ {message}", icon="❌")
    elif n_type == "warning":
        st.toast(f"⚠️ {message}", icon="⚠️")
    else:
        st.toast(f"ℹ️ {message}", icon="ℹ️")


# =====================================================================
# 4. GSTIN & PHONE VALIDATION ENGINE
# =====================================================================
GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"


def validate_gstin(gstin_str: str) -> bool:
    if not gstin_str or not gstin_str.strip():
        return True
    return bool(re.match(GSTIN_REGEX, gstin_str.strip().upper()))


COUNTRY_CALLING_CODES = {
    "🇮🇳 India (+91)": {"code": "+91", "min": 10, "max": 10, "regex": r"^[6-9]\d{9}$", "msg": "Please enter a valid 10-digit mobile number."},
    "🇺🇸 United States (+1)": {"code": "+1", "min": 10, "max": 10, "regex": r"^\d{10}$", "msg": "Please enter a valid 10-digit US phone number."},
    "🇨🇦 Canada (+1)": {"code": "+1", "min": 10, "max": 10, "regex": r"^\d{10}$", "msg": "Please enter a valid 10-digit Canadian phone number."},
    "🇬🇧 United Kingdom (+44)": {"code": "+44", "min": 9, "max": 11, "regex": r"^\d{9,11}$", "msg": "Please enter a valid UK phone number (9 to 11 digits)."},
    "🇦🇪 UAE (+971)": {"code": "+971", "min": 9, "max": 9, "regex": r"^5\d{8}$", "msg": "Please enter a valid UAE mobile number starting with 5 (9 digits)."},
    "🌍 Other Country (Custom)": {"code": "", "min": 6, "max": 15, "regex": r"^\d{6,15}$", "msg": "Please enter a valid phone number (6 to 15 digits)."}
}


def validate_international_phone(country_label: str, raw_number: str) -> Tuple[bool, str, str]:
    clean_num = re.sub(r'[\s\-\(\)]', '', raw_number.strip())
    config = COUNTRY_CALLING_CODES.get(country_label, COUNTRY_CALLING_CODES["🌍 Other Country (Custom)"])
    dial_code = config["code"]

    if not clean_num:
        return False, "", "Mobile number cannot be empty."
    if not clean_num.isdigit():
        return False, "", "Mobile number must contain digits only."
    if not re.match(config["regex"], clean_num):
        return False, "", config["msg"]

    e164_formatted = f"{dial_code}{clean_num}" if dial_code else f"+{clean_num}"
    return True, e164_formatted, ""


def check_password_strength(password: str) -> Dict[str, Any]:
    length_ok = len(password) >= 8
    upper_ok = bool(re.search(r'[A-Z]', password))
    lower_ok = bool(re.search(r'[a-z]', password))
    number_ok = bool(re.search(r'\d', password))
    special_ok = bool(re.search(r'[!@#$%^&*(),.?":{}|<>]', password))
    is_valid = length_ok and upper_ok and lower_ok and number_ok and special_ok

    return {
        "is_valid": is_valid,
        "length": length_ok,
        "upper": upper_ok,
        "lower": lower_ok,
        "number": number_ok,
        "special": special_ok
    }


def render_password_checklist(criteria: Dict[str, Any]):
    def item(ok: bool, text: str) -> str:
        color = "#10B981" if ok else "#94A3B8"
        icon = "✓" if ok else "○"
        return f"<div style='color: {color}; font-size: 0.8rem; line-height: 1.4;'>{icon} {text}</div>"

    html = f"""
    <div style='background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px; margin-top: 6px; margin-bottom: 12px;'>
        <div style='font-size: 0.8rem; font-weight: 700; color: #475569; margin-bottom: 4px;'>Password Security Policy:</div>
        {item(criteria["length"], "At least 8 characters")}
        {item(criteria["upper"], "At least 1 uppercase letter (A-Z)")}
        {item(criteria["lower"], "At least 1 lowercase letter (a-z)")}
        {item(criteria["number"], "At least 1 number (0-9)")}
        {item(criteria["special"], "At least 1 special character (!@#$%^&*)")}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# =====================================================================
# 5. MULTI-TENANT DATABASE ENGINE
# =====================================================================
class DatabaseAdapter:
    @staticmethod
    def is_pg() -> bool:
        return POSTGRES_AVAILABLE and DATABASE_URL.startswith("postgres")

    @classmethod
    def get_connection(cls):
        if cls.is_pg():
            return psycopg2.connect(DATABASE_URL)
        conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def execute(cls, query: str, params: tuple = (), fetch: str = "none") -> Any:
        conn = cls.get_connection()
        is_postgres = cls.is_pg()

        if is_postgres:
            query = query.replace("?", "%s")
            query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            query = query.replace("DATETIME DEFAULT CURRENT_TIMESTAMP", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor()

        try:
            cursor.execute(query, params)
            result = None
            if fetch == "one":
                row = cursor.fetchone()
                result = dict(row) if row else None
            elif fetch == "all":
                rows = cursor.fetchall()
                result = [dict(r) for r in rows]
            elif fetch == "lastrowid":
                if is_postgres:
                    result = cursor.fetchone()
                    if result:
                        result = list(result.values())[0]
                else:
                    result = cursor.lastrowid

            conn.commit()
            return result
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def read_df(cls, query: str, params: list = None) -> pd.DataFrame:
        conn = cls.get_connection()
        is_postgres = cls.is_pg()
        if is_postgres and params:
            query = query.replace("?", "%s")
        try:
            return pd.read_sql_query(query, conn, params=params)
        finally:
            conn.close()


def init_saas_db():
    conn = DatabaseAdapter.get_connection()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS tenants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_name TEXT NOT NULL,
        plan_tier TEXT DEFAULT 'Free',
        subscription_status TEXT DEFAULT 'Active',
        max_products INTEGER DEFAULT 500,
        trial_ends_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER,
        owner_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        mobile TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'OWNER',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS business_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER UNIQUE NOT NULL,
        business_name TEXT DEFAULT 'My Enterprise Ltd.',
        address TEXT DEFAULT '123 Industrial Hub, Trade Center',
        phone TEXT DEFAULT '+91 98765 43210',
        email TEXT DEFAULT 'contact@enterprise.com',
        gstin TEXT DEFAULT '27AAAAA0000A1Z5',
        state TEXT DEFAULT 'Maharashtra',
        sales_prefix TEXT DEFAULT 'INV-',
        purchase_prefix TEXT DEFAULT 'PUR-',
        sku_prefix TEXT DEFAULT 'PRD-',
        default_unit TEXT DEFAULT 'Piece',
        default_gst REAL DEFAULT 18.0,
        default_barcode_type TEXT DEFAULT 'Code 128',
        default_label_size TEXT DEFAULT '3 x 2 inch',
        allow_negative_stock INTEGER DEFAULT 0,
        low_stock_threshold INTEGER DEFAULT 5,
        terms_and_conditions TEXT DEFAULT 'Goods once sold will not be taken back without original bill. Payment due within terms.',
        signature_text TEXT DEFAULT 'Authorized Signatory',
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        UNIQUE(tenant_id, name),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        UNIQUE(tenant_id, name),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        sku TEXT NOT NULL,
        barcode TEXT NOT NULL,
        category TEXT DEFAULT 'General',
        subcategory TEXT,
        brand TEXT,
        description TEXT,
        unit TEXT DEFAULT 'Piece',
        purchase_price REAL DEFAULT 0.0,
        mrp REAL DEFAULT 0.0,
        selling_price REAL DEFAULT 0.0,
        wholesale_price REAL DEFAULT 0.0,
        gst_rate REAL DEFAULT 18.0,
        hsn_sac TEXT,
        minimum_stock INTEGER DEFAULT 5,
        maximum_stock INTEGER DEFAULT 1000,
        status TEXT DEFAULT 'Active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, sku),
        UNIQUE(tenant_id, barcode),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        product_id INTEGER PRIMARY KEY,
        tenant_id INTEGER NOT NULL,
        current_stock REAL DEFAULT 0.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        product_id INTEGER NOT NULL,
        operation TEXT NOT NULL,
        quantity REAL NOT NULL,
        stock_before REAL NOT NULL,
        stock_after REAL NOT NULL,
        reference TEXT,
        notes TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS creditor_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        business_name TEXT,
        phone TEXT,
        gstin TEXT,
        balance REAL DEFAULT 0.0,
        address TEXT,
        email TEXT,
        UNIQUE(tenant_id, name),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS debitor_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        business_name TEXT,
        phone TEXT,
        gstin TEXT,
        balance REAL DEFAULT 0.0,
        address TEXT,
        email TEXT,
        UNIQUE(tenant_id, name),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS billing_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        invoice_number TEXT NOT NULL,
        transaction_type TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_name TEXT NOT NULL,
        business_name TEXT,
        phone TEXT,
        gstin TEXT,
        invoice_date TEXT NOT NULL,
        due_date TEXT,
        subtotal REAL DEFAULT 0.0,
        gst_total REAL DEFAULT 0.0,
        grand_total REAL DEFAULT 0.0,
        paid_amount REAL DEFAULT 0.0,
        outstanding_amount REAL DEFAULT 0.0,
        payment_status TEXT DEFAULT 'Unpaid',
        payment_method TEXT DEFAULT 'Cash',
        status TEXT DEFAULT 'Active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, invoice_number),
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS billing_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        transaction_id INTEGER NOT NULL,
        product_id INTEGER,
        item_name TEXT NOT NULL,
        item_price REAL DEFAULT 0.0,
        mrp REAL DEFAULT 0.0,
        selling_price REAL DEFAULT 0.0,
        quantity REAL NOT NULL,
        gst_rate REAL DEFAULT 0.0,
        gst_amount REAL DEFAULT 0.0,
        total_amount REAL DEFAULT 0.0,
        FOREIGN KEY(transaction_id) REFERENCES billing_transactions(id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        payment_date TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_name TEXT NOT NULL,
        payment_method TEXT NOT NULL,
        reference_number TEXT,
        amount REAL NOT NULL,
        notes TEXT,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS ledgers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_name TEXT NOT NULL,
        particular TEXT NOT NULL,
        debit REAL DEFAULT 0.0,
        credit REAL DEFAULT 0.0,
        balance REAL DEFAULT 0.0,
        reference TEXT,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        action TEXT NOT NULL,
        details TEXT NOT NULL,
        FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
    );
    """)

    c.execute("SELECT COUNT(*) FROM users WHERE role = 'SUPERADMIN'")
    if c.fetchone()[0] == 0:
        admin_pass = hashlib.pbkdf2_hmac('sha256', b"Admin@StockLabel2026", b"stocklabel_salt", 100000).hex()
        c.execute("""
            INSERT INTO users (tenant_id, owner_name, email, mobile, password_hash, role)
            VALUES (NULL, 'SaaS Super Administrator', 'admin@stocklabel.ai', '+919999999999', ?, 'SUPERADMIN')
        """, (admin_pass,))

    conn.commit()
    conn.close()


init_saas_db()


def run_database_migrations():
    conn = DatabaseAdapter.get_connection()
    c = conn.cursor()
    for tbl in ["creditor_accounts", "debitor_accounts"]:
        for col, col_type in [("address", "TEXT"), ("email", "TEXT")]:
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type};")
            except Exception:
                pass
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS payments_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            party_type TEXT NOT NULL,
            party_name TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            reference_number TEXT,
            amount REAL NOT NULL,
            notes TEXT,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
        """)
    except Exception:
        pass
    conn.commit()
    conn.close()

run_database_migrations()


# =====================================================================
# 6. AUTHENTICATION & DATA HELPERS (USING BUILT-IN HASHHLIB)
# =====================================================================
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 100000).hex()
    return f"{salt}:{pwd_hash}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if ":" not in stored_hash:
            # Fallback for old default admin hash
            legacy_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), b"stocklabel_salt", 100000).hex()
            return legacy_hash == stored_hash
        salt, pwd_hash = stored_hash.split(':')
        check_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 100000).hex()
        return check_hash == pwd_hash
    except Exception:
        return False


def register_tenant(business_name: str, owner_name: str, email: str, e164_mobile: str, password: str) -> Tuple[bool, str]:
    try:
        existing_user = DatabaseAdapter.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),), fetch="one")
        if existing_user:
            return False, "An account with this email address already exists. Please log in."

        t_id = DatabaseAdapter.execute("""
            INSERT INTO tenants (business_name, plan_tier, subscription_status, max_products, trial_ends_at)
            VALUES (?, 'Free', 'Active', 500, ?)
        """, (business_name.strip(), (datetime.datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")), fetch="lastrowid")

        if not t_id:
            row = DatabaseAdapter.execute("SELECT MAX(id) FROM tenants", fetch="one")
            t_id = list(row.values())[0]

        p_hash = hash_password(password)
        DatabaseAdapter.execute("""
            INSERT INTO users (tenant_id, owner_name, email, mobile, password_hash, role)
            VALUES (?, ?, ?, ?, ?, 'OWNER')
        """, (t_id, owner_name.strip(), email.strip().lower(), e164_mobile, p_hash))

        DatabaseAdapter.execute("""
            INSERT INTO business_settings (tenant_id, business_name, email, phone)
            VALUES (?, ?, ?, ?)
        """, (t_id, business_name.strip(), email.strip().lower(), e164_mobile))

        units = ["Piece", "Kg", "Gram", "Litre", "ML", "Meter", "CM", "Box", "Carton", "Dozen", "Pair", "Set", "Pack", "Bottle", "Bag", "Service"]
        for u in units:
            DatabaseAdapter.execute("INSERT INTO units (tenant_id, name) VALUES (?, ?)", (t_id, u))

        cats = ["Electronics", "Hardware", "Grocery", "Cosmetics", "Garments", "General", "Services"]
        for cat in cats:
            DatabaseAdapter.execute("INSERT INTO categories (tenant_id, name) VALUES (?, ?)", (t_id, cat))

        return True, "Business tenant successfully created! Please log in."
    except Exception as e:
        return False, f"Registration failed: {str(e)}"


def log_tenant_audit(action: str, details: str):
    t_id = st.session_state.tenant_id
    if t_id:
        DatabaseAdapter.execute("INSERT INTO audit_logs (tenant_id, action, details) VALUES (?, ?, ?)", (t_id, action, details))


def get_tenant_settings() -> Dict[str, Any]:
    t_id = st.session_state.tenant_id
    if not t_id:
        return {}
    row = DatabaseAdapter.execute("SELECT * FROM business_settings WHERE tenant_id = ?", (t_id,), fetch="one")
    return row or {}


def get_next_tenant_product_id() -> int:
    t_id = st.session_state.tenant_id
    row = DatabaseAdapter.execute("SELECT MAX(id) as max_id FROM products WHERE tenant_id = ?", (t_id,), fetch="one")
    val = row["max_id"] if row and row["max_id"] is not None else 0
    return int(val) + 1


def format_currency(val: float) -> str:
    return f"₹{val:,.2f}"


def format_error(e: Exception) -> str:
    msg = str(e)
    if "UNIQUE constraint failed: products.sku" in msg or "products_tenant_id_sku_key" in msg:
        return "This SKU code is already in use in your catalog. Please enter a different SKU."
    if "UNIQUE constraint failed: products.barcode" in msg or "products_tenant_id_barcode_key" in msg:
        return "This Barcode number is already assigned to another item. Please use a unique barcode."
    if "UNIQUE constraint failed: billing_transactions.invoice_number" in msg or "billing_transactions_tenant_id_invoice_number_key" in msg:
        return "This Invoice Number already exists in your records. Please use a unique invoice number."
    return f"Operation failed: {msg}"


# =====================================================================
# 7. INVENTORY, PRODUCT SYNC & LEDGER OPERATIONS
# =====================================================================
def get_or_create_product_from_purchase(t_id: int, item_name: str, item_price: float, gst_rate: float) -> int:
    clean_name = item_name.strip()
    existing = DatabaseAdapter.execute("SELECT id FROM products WHERE tenant_id = ? AND LOWER(name) = LOWER(?)", (t_id, clean_name), fetch="one")
    if existing:
        return int(existing["id"])

    next_id = get_next_tenant_product_id()
    settings = get_tenant_settings()
    sku_prefix = settings.get("sku_prefix", "STK-")
    auto_sku = f"{sku_prefix}{next_id:06d}"
    auto_barcode = f"890{t_id:03d}{next_id:06d}"

    while DatabaseAdapter.execute("SELECT id FROM products WHERE tenant_id = ? AND sku = ?", (t_id, auto_sku), fetch="one"):
        next_id += 1
        auto_sku = f"{sku_prefix}{next_id:06d}"

    while DatabaseAdapter.execute("SELECT id FROM products WHERE tenant_id = ? AND barcode = ?", (t_id, auto_barcode), fetch="one"):
        auto_barcode = f"890{t_id:03d}{int(auto_barcode[-6:]) + 1:06d}"

    suggested_selling = item_price * 1.2
    suggested_mrp = item_price * 1.45

    new_pid = DatabaseAdapter.execute("""
        INSERT INTO products (tenant_id, name, sku, barcode, category, unit, purchase_price, mrp, selling_price, gst_rate, minimum_stock)
        VALUES (?, ?, ?, ?, 'General', 'Piece', ?, ?, ?, ?, 5)
    """, (t_id, clean_name, auto_sku, auto_barcode, item_price, suggested_mrp, suggested_selling, gst_rate), fetch="lastrowid")

    DatabaseAdapter.execute("INSERT INTO inventory (product_id, tenant_id, current_stock) VALUES (?, ?, 0.0)", (new_pid, t_id))
    log_tenant_audit("AUTO_PRODUCT_CREATE", f"Auto-created product ID {new_pid}: {clean_name} (SKU: {auto_sku})")
    return int(new_pid)


def get_or_find_product_from_sale(t_id: int, item_name: str) -> Optional[int]:
    clean_name = item_name.strip()
    existing = DatabaseAdapter.execute("SELECT id FROM products WHERE tenant_id = ? AND LOWER(name) = LOWER(?)", (t_id, clean_name), fetch="one")
    return int(existing["id"]) if existing else None


def adjust_inventory_stock(product_id: int, qty_change: float, operation: str, reference: str = "", notes: str = "") -> Tuple[bool, str]:
    t_id = st.session_state.tenant_id
    try:
        settings = get_tenant_settings()
        allow_neg = bool(settings.get("allow_negative_stock", 0))

        cur_row = DatabaseAdapter.execute("SELECT current_stock FROM inventory WHERE product_id = ? AND tenant_id = ?", (product_id, t_id), fetch="one")
        current_stock = float(cur_row["current_stock"]) if cur_row else 0.0
        new_stock = current_stock + qty_change

        if new_stock < 0 and not allow_neg:
            return False, f"Insufficient stock! Available: {current_stock}, Requested reduction: {abs(qty_change)}"

        if DatabaseAdapter.is_pg():
            DatabaseAdapter.execute("""
                INSERT INTO inventory (product_id, tenant_id, current_stock, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (product_id) DO UPDATE SET current_stock = EXCLUDED.current_stock, updated_at = CURRENT_TIMESTAMP
            """, (product_id, t_id, new_stock))
        else:
            DatabaseAdapter.execute("INSERT OR REPLACE INTO inventory (product_id, tenant_id, current_stock, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (product_id, t_id, new_stock))

        DatabaseAdapter.execute("""
            INSERT INTO inventory_movements (tenant_id, product_id, operation, quantity, stock_before, stock_after, reference, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (t_id, product_id, operation, qty_change, current_stock, new_stock, reference, notes))

        log_tenant_audit("STOCK_CHANGE", f"Product ID {product_id}: {operation} {qty_change:+.1f} (Balance: {new_stock:.1f})")
        return True, "Stock updated successfully."
    except Exception as e:
        return False, format_error(e)


def update_party_ledger(party_type: str, party_name: str, particular: str, debit: float, credit: float, reference: str = ""):
    t_id = st.session_state.tenant_id
    last_row = DatabaseAdapter.execute("""
        SELECT balance FROM ledgers WHERE tenant_id = ? AND party_type = ? AND party_name = ? ORDER BY id DESC LIMIT 1
    """, (t_id, party_type, party_name), fetch="one")
    prev_balance = float(last_row["balance"]) if last_row else 0.0

    if party_type == "CREDITOR":
        new_balance = prev_balance + credit - debit
    else:
        new_balance = prev_balance + debit - credit

    today_str = date.today().strftime("%Y-%m-%d")
    DatabaseAdapter.execute("""
        INSERT INTO ledgers (tenant_id, date, party_type, party_name, particular, debit, credit, balance, reference)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (t_id, today_str, party_type, party_name, particular, debit, credit, new_balance, reference))


# =====================================================================
# 8. BARCODE & LABEL GENERATION ENGINE
# =====================================================================
def generate_barcode_image(code_value: str, btype: str = "Code 128") -> Optional[Image.Image]:
    if not code_value:
        return None
    try:
        if btype == "QR Code":
            qr = qrcode.QRCode(version=1, box_size=8, border=2)
            qr.add_data(code_value)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white").convert('RGB')

        writer = ImageWriter()
        writer.set_options({'module_height': 15.0, 'font_size': 8, 'text_distance': 3.0, 'quiet_zone': 2.0})

        if btype == "EAN-13":
            clean_code = re.sub(r'\D', '', code_value).zfill(12)[:12]
            bc = EAN13(clean_code, writer=writer)
        elif btype == "EAN-8":
            clean_code = re.sub(r'\D', '', code_value).zfill(7)[:7]
            bc = EAN8(clean_code, writer=writer)
        elif btype == "UPC-A":
            clean_code = re.sub(r'\D', '', code_value).zfill(11)[:11]
            bc = UPCA(clean_code, writer=writer)
        elif btype == "Code 39":
            clean_code = re.sub(r'[^A-Z0-9\-\.\ \$\/\+\%]', '', code_value.upper())
            bc = Code39(clean_code, writer=writer, add_checksum=False)
        else:
            bc = Code128(code_value, writer=writer)

        buf = io.BytesIO()
        bc.write(buf)
        buf.seek(0)
        return Image.open(buf).convert('RGB')
    except Exception:
        try:
            buf = io.BytesIO()
            Code128(code_value, writer=ImageWriter()).write(buf)
            buf.seek(0)
            return Image.open(buf).convert('RGB')
        except Exception:
            return None


def render_label_preview(product_dict: Dict[str, Any], settings: Dict[str, Any], template_opts: Dict[str, Any]) -> Image.Image:
    w_px = int(template_opts.get("width_in", 3.0) * 100)
    h_px = int(template_opts.get("height_in", 2.0) * 100)
    img = Image.new('RGB', (w_px, h_px), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    draw.rectangle([(2, 2), (w_px - 3, h_px - 3)], outline=(200, 200, 200), width=1)

    y = 6
    if template_opts.get("show_biz", True):
        bname = (settings.get("business_name") or "BUSINESS").upper()
        draw.text((w_px // 2, y), bname, fill=(30, 30, 30), anchor="mt")
        y += 18
        draw.line([(8, y), (w_px - 8, y)], fill=(225, 225, 225), width=1)
        y += 4

    pname = str(product_dict.get("name", "Product Title"))
    if len(pname) > 28:
        pname = pname[:26] + ".."
    draw.text((w_px // 2, y), pname, fill=(0, 0, 0), anchor="mt")
    y += 18

    sub_texts = []
    if template_opts.get("show_sku", True):
        sub_texts.append(f"SKU: {product_dict.get('sku', '')}")
    if template_opts.get("show_hsn", False) and product_dict.get('hsn_sac'):
        sub_texts.append(f"HSN: {product_dict.get('hsn_sac')}")
    if sub_texts:
        draw.text((w_px // 2, y), " | ".join(sub_texts), fill=(80, 80, 80), anchor="mt")
        y += 14

    barcode_val = str(product_dict.get("barcode", "00000000"))
    bimg = generate_barcode_image(barcode_val, template_opts.get("barcode_type", "Code 128"))
    if bimg:
        bc_target_w = int(w_px * 0.85)
        bc_target_h = int(h_px * 0.35)
        bimg.thumbnail((bc_target_w, bc_target_h))
        bx = (w_px - bimg.width) // 2
        img.paste(bimg, (bx, y))
        y += bimg.height + 4

    p_texts = []
    if template_opts.get("show_mrp", True) and product_dict.get("mrp", 0) > 0:
        p_texts.append(f"MRP: ₹{product_dict.get('mrp', 0):,.2f}")
    if template_opts.get("show_sp", True):
        p_texts.append(f"PRICE: ₹{product_dict.get('selling_price', 0):,.2f}")

    if p_texts:
        draw.text((w_px // 2, y), "  ".join(p_texts), fill=(0, 120, 0), anchor="mt")
        y += 14

    if template_opts.get("show_gst", False):
        draw.text((w_px // 2, y), f"(Incl. {product_dict.get('gst_rate', 0)}% GST)", fill=(100, 100, 100), anchor="mt")

    return img


# =====================================================================
# 9. REPORTLAB PDF INVOICE ENGINE
# =====================================================================
def generate_invoice_pdf(tx_data: Dict[str, Any], items: List[Dict[str, Any]], settings: Dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    story = []
    styles = getSampleStyleSheet()

    is_purchase = (tx_data["transaction_type"] == "PURCHASE")
    doc_title = "PURCHASE INVOICE" if is_purchase else "TAX INVOICE / SALES BILL"

    header_data = [
        [
            Paragraph(
                f"<b>{settings.get('business_name', 'ENTERPRISE')}</b><br/>{settings.get('address', '')}<br/>Phone: {settings.get('phone', '')}<br/>GSTIN: {settings.get('gstin', '')}",
                styles['Normal']),
            Paragraph(
                f"<font size=14 color='#1E3A8A'><b>{doc_title}</b></font><br/><b>Invoice No:</b> {tx_data['invoice_number']}<br/><b>Date:</b> {tx_data['invoice_date']}<br/><b>Status:</b> {tx_data['payment_status']}",
                styles['Normal'])
        ]
    ]
    t_header = Table(header_data, colWidths=[3.2 * inch, 4.0 * inch])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 10))

    party_title = "SUPPLIER (CREDITOR) DETAILS:" if is_purchase else "CUSTOMER (DEBITOR) DETAILS:"
    party_text = f"<b>Business Name:</b> {tx_data.get('party_name', '')}<br/><b>Mobile:</b> {tx_data.get('phone', '') or 'N/A'} | <b>GSTIN:</b> {tx_data.get('gstin', '') or 'N/A'}"

    party_data = [
        [Paragraph(f"<b>{party_title}</b>", styles['Normal'])],
        [Paragraph(party_text, styles['Normal'])]
    ]
    t_party = Table(party_data, colWidths=[7.2 * inch])
    t_party.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t_party)
    story.append(Spacer(1, 12))

    if is_purchase:
        headers = ["#", "Item Description", "Item Price (₹)", "Qty", "GST %", "GST Amt (₹)", "Total (₹)"]
        col_w = [0.3 * inch, 2.7 * inch, 0.9 * inch, 0.6 * inch, 0.6 * inch, 0.9 * inch, 1.2 * inch]
    else:
        headers = ["#", "Item Description", "MRP (₹)", "Rate (₹)", "Qty", "GST %", "Total (₹)"]
        col_w = [0.3 * inch, 2.4 * inch, 0.8 * inch, 0.9 * inch, 0.6 * inch, 0.8 * inch, 1.4 * inch]

    table_rows = [headers]
    for idx, item in enumerate(items, 1):
        if is_purchase:
            row = [
                str(idx),
                item.get('item_name', ''),
                f"{item.get('item_price', 0):,.2f}",
                str(item.get('quantity', 1)),
                f"{item.get('gst_rate', 0)}%",
                f"{item.get('gst_amount', 0):,.2f}",
                f"{item.get('total_amount', 0):,.2f}"
            ]
        else:
            row = [
                str(idx),
                item.get('item_name', ''),
                f"{item.get('mrp', 0):,.2f}",
                f"{item.get('selling_price', 0):,.2f}",
                str(item.get('quantity', 1)),
                f"{item.get('gst_rate', 0)}%",
                f"{item.get('total_amount', 0):,.2f}"
            ]
        table_rows.append(row)

    t_items = Table(table_rows, colWidths=col_w)
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_items)
    story.append(Spacer(1, 10))

    summary_data = [
        ["Subtotal Taxable:", f"₹{tx_data.get('subtotal', 0):,.2f}"],
        ["Total GST:", f"₹{tx_data.get('gst_total', 0):,.2f}"],
        ["Grand Total:", f"₹{tx_data.get('grand_total', 0):,.2f}"],
        ["Paid Amount:", f"₹{tx_data.get('paid_amount', 0):,.2f}"],
        ["Balance Outstanding:", f"₹{tx_data.get('outstanding_amount', 0):,.2f}"]
    ]
    t_sum = Table(summary_data, colWidths=[5.4 * inch, 1.8 * inch])
    t_sum.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#DC2626")),
        ('LINEABOVE', (0, 2), (1, 2), 1, colors.HexColor("#1E3A8A")),
        ('PADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_sum)
    story.append(Spacer(1, 15))

    terms_txt = settings.get('terms_and_conditions', '')
    sig_txt = settings.get('signature_text', 'Authorized Signatory')
    bottom_data = [
        [Paragraph(f"<b>Terms & Conditions:</b><br/>{terms_txt}", styles['Normal']),
         Paragraph(f"<br/><br/><br/>________________________<br/><b>{sig_txt}</b>", styles['Normal'])]
    ]
    t_bot = Table(bottom_data, colWidths=[4.5 * inch, 2.7 * inch])
    t_bot.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
    ]))
    story.append(t_bot)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# =====================================================================
# 10. SEED TENANT DEMO DATA
# =====================================================================
def seed_tenant_demo_data(t_id: int):
    sample_products = [
        ("Wireless Optical Mouse", "PRD-00001", "890123456701", "Electronics", "Peripherals", "LogiTech",
         "2.4GHz USB Mouse", "Piece", 350.0, 799.0, 649.0, 500.0, 18.0, "8471", 10, 200, 45),
        ("LED Energy Bulb 9W", "PRD-00002", "890123456702", "Hardware", "Lighting", "Philips", "Cool Daylight B22",
         "Piece", 45.0, 120.0, 95.0, 70.0, 12.0, "8539", 20, 500, 120),
        ("Basmati Rice Premium 5kg", "PRD-00003", "890123456703", "Grocery", "Grains", "IndiaGate",
         "Royal Feast Aged Grain", "Bag", 420.0, 650.0, 580.0, 500.0, 5.0, "1006", 5, 50, 18),
        ("Ergonomic Mesh Chair", "PRD-00004", "890123456704", "Furniture", "Seating", "Featherlite",
         "High-back Lumbar Support", "Piece", 3200.0, 7500.0, 5999.0, 4500.0, 18.0, "9401", 2, 20, 4),
        ("Fast Mobile Charger 33W", "PRD-00005", "890123456705", "Electronics", "Accessories", "Mi",
         "Type-C Fast Adapter", "Piece", 280.0, 699.0, 499.0, 390.0, 18.0, "8504", 15, 100, 2),
        ("Men Formal Cotton Shirt", "PRD-00006", "890123456706", "Garments", "Apparel", "Raymond",
         "Classic Fit Light Blue", "Piece", 450.0, 1299.0, 999.0, 750.0, 5.0, "6205", 10, 80, 25),
    ]

    for p in sample_products:
        pid = DatabaseAdapter.execute("""
            INSERT INTO products 
            (tenant_id, name, sku, barcode, category, subcategory, brand, description, unit, purchase_price, mrp, selling_price, wholesale_price, gst_rate, hsn_sac, minimum_stock, maximum_stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (t_id, *p[:16]), fetch="lastrowid")

        if pid:
            initial_stock = p[16]
            DatabaseAdapter.execute("INSERT INTO inventory (product_id, tenant_id, current_stock) VALUES (?, ?, ?)", (pid, t_id, initial_stock))
            DatabaseAdapter.execute("""
                INSERT INTO inventory_movements (tenant_id, product_id, operation, quantity, stock_before, stock_after, reference, notes)
                VALUES (?, ?, 'Opening Stock', ?, 0, ?, 'INIT', 'Demo initialization')
            """, (t_id, pid, initial_stock, initial_stock))

    DatabaseAdapter.execute("""
        INSERT INTO creditor_accounts (tenant_id, name, business_name, phone, gstin, balance)
        VALUES (?, 'Apex Distributors', 'Apex Supplies Ltd', '9811122233', '27ABCDE1234F1Z1', 0)
    """, (t_id,))
    DatabaseAdapter.execute("""
        INSERT INTO debitor_accounts (tenant_id, name, business_name, phone, gstin, balance)
        VALUES (?, 'Metro Retail Hub', 'Metro Stores', '9877788899', '27XYZAB9876C1Z2', 0)
    """, (t_id,))

    log_tenant_audit("DEMO_LOADED", "Sample demo products and party accounts initialized.")


# =====================================================================
# 11. POLISHED ENTERPRISE UI STYLES
# =====================================================================
CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');
    * { font-family: 'Plus Jakarta Sans', sans-serif; }
    .stApp { background-color: #F8FAFC; }

    .landing-header {
        position: sticky;
        top: 0;
        z-index: 999;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(8px);
        border-bottom: 1px solid #E2E8F0;
        padding: 14px 28px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 12px;
        margin-bottom: 24px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.02);
    }
    .landing-logo {
        font-size: 1.35rem;
        font-weight: 800;
        color: #1E3A8A;
        letter-spacing: -0.5px;
    }
    .hero-container {
        padding: 40px 10px 30px 10px;
    }
    .hero-badge {
        display: inline-block;
        background: #EFF6FF;
        color: #1D4ED8;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 700;
        border: 1px solid #DBEAFE;
        margin-bottom: 16px;
    }
    .hero-title {
        font-size: 3.1rem;
        font-weight: 800;
        line-height: 1.15;
        color: #0F172A;
        letter-spacing: -1px;
        margin-bottom: 18px;
    }
    .hero-title span {
        background: linear-gradient(135deg, #1E40AF 0%, #3B82F6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .hero-subtitle {
        font-size: 1.1rem;
        color: #475569;
        line-height: 1.6;
        margin-bottom: 28px;
    }
    .trust-strip {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 16px 24px;
        display: flex;
        justify-content: space-around;
        flex-wrap: wrap;
        gap: 16px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.02);
        margin: 20px 0 40px 0;
    }
    .trust-item {
        font-size: 0.88rem;
        font-weight: 700;
        color: #334155;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .feat-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 24px;
        height: 100%;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    .feat-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 20px rgba(30, 58, 138, 0.06);
        border-color: #BFDBFE;
    }
    .feat-icon {
        font-size: 1.8rem;
        margin-bottom: 12px;
    }
    .feat-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #0F172A;
        margin-bottom: 8px;
    }
    .feat-desc {
        font-size: 0.85rem;
        color: #64748B;
        line-height: 1.5;
    }
    .flow-badge {
        background: #FFFFFF;
        border: 1px dashed #3B82F6;
        border-radius: 12px;
        padding: 18px;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .cta-banner {
        background: linear-gradient(135deg, #1E3A8A 0%, #1E40AF 100%);
        border-radius: 16px;
        padding: 48px 32px;
        text-align: center;
        color: white;
        margin: 40px 0;
        box-shadow: 0 12px 30px rgba(30, 58, 138, 0.18);
    }
    .landing-footer {
        background: #FFFFFF;
        border-top: 1px solid #E2E8F0;
        padding: 40px 20px 20px 20px;
        margin-top: 50px;
        border-radius: 12px;
    }
    .creator-box {
        background: #F1F5F9;
        border: 1px solid #CBD5E1;
        border-radius: 10px;
        padding: 18px;
        text-align: center;
        margin-top: 24px;
    }
    .metric-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 14px 18px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.02);
        margin-bottom: 12px;
    }
    .metric-label { font-size: 0.8rem; font-weight: 600; color: #64748B; text-transform: uppercase; }
    .metric-value { font-size: 1.5rem; font-weight: 700; color: #0F172A; margin-top: 4px; }
    .metric-sub { font-size: 0.75rem; color: #10B981; margin-top: 2px; }
    .metric-sub.danger { color: #EF4444; }

    .total-box {
        background: linear-gradient(135deg, #1E3A8A 0%, #1E40AF 100%);
        color: white;
        padding: 20px;
        border-radius: 12px;
        text-align: right;
        box-shadow: 0 4px 12px rgba(30, 58, 138, 0.15);
    }
    .total-box-label { font-size: 0.9rem; opacity: 0.85; }
    .total-box-amount { font-size: 2.2rem; font-weight: 800; }
    .section-head {
        font-size: 0.95rem;
        font-weight: 700;
        color: #1E293B;
        margin: 14px 0 8px 0;
        display: flex;
        align-items: center;
        gap: 6px;
    }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# =====================================================================
# 12. PUBLIC LANDING PAGE & ONBOARDING ROUTER
# =====================================================================
if not st.session_state.authenticated:
    nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([3, 4, 1.2, 1.2])
    with nav_col1:
        st.markdown("<div class='landing-logo'>🏷️ StockLabel AI</div>", unsafe_allow_html=True)
    with nav_col2:
        st.markdown("""
        <div style='display: flex; gap: 20px; padding-top: 8px; font-weight: 600; font-size: 0.9rem;'>
            <a href='#features' style='text-decoration: none; color: #475569;'>Features</a>
            <a href='#workflow' style='text-decoration: none; color: #475569;'>How It Works</a>
            <a href='#industries' style='text-decoration: none; color: #475569;'>Industries</a>
            <a href='#security' style='text-decoration: none; color: #475569;'>Security</a>
        </div>
        """, unsafe_allow_html=True)
    with nav_col3:
        if st.button("Log In", use_container_width=True):
            st.session_state.auth_mode = "LOGIN"
    with nav_col4:
        if st.button("Create new account", type="primary", use_container_width=True):
            st.session_state.auth_mode = "REGISTER"

    st.markdown("<hr style='margin: 8px 0 24px 0; border: none; border-top: 1px solid #E2E8F0;'/>", unsafe_allow_html=True)

    if st.session_state.auth_mode == "LOGIN":
        with st.container():
            st.markdown("### 🔐 Log In to Your Business Workspace")
            with st.form("modal_login_form"):
                log_email = st.text_input("Business Email/User ID*", placeholder="owner@company.com")
                log_pass = st.text_input("Password*", type="password", placeholder="••••••••")
                f_c1, f_c2 = st.columns(2)
                if f_c1.form_submit_button("Log In", type="primary", use_container_width=True):
                    if not log_email or not log_pass:
                        notify("Please provide both email and password.", "error")
                    else:
                        user = DatabaseAdapter.execute("SELECT * FROM users WHERE email = ?", (log_email.strip().lower(),), fetch="one")
                        if user and verify_password(log_pass, user["password_hash"]):
                            st.session_state.authenticated = True
                            st.session_state.user_id = user["id"]
                            st.session_state.tenant_id = user["tenant_id"]
                            st.session_state.user_role = user["role"]
                            st.session_state.auth_mode = "LANDING"
                            notify("Authenticated successfully!", "success")
                            st.rerun()
                        else:
                            notify("Invalid email address or password.", "error")
                if f_c2.form_submit_button("Back to Homepage", use_container_width=True):
                    st.session_state.auth_mode = "LANDING"
                    st.rerun()
        st.stop()

    elif st.session_state.auth_mode == "REGISTER":
        with st.container():
            st.markdown("### 🚀 Create Your Business Account")
            with st.form("modal_reg_form"):
                r_biz = st.text_input("Business / Company Name*", placeholder="e.g. Apex Global Traders")
                r_owner = st.text_input("Owner Full Name*", placeholder="e.g. John Doe")
                r_email = st.text_input("Business Email/User ID*", placeholder="e.g. john@apexglobal.com")

                r_c1, r_c2 = st.columns([1.2, 1.8])
                selected_country = r_c1.selectbox("Country*", list(COUNTRY_CALLING_CODES.keys()), index=0)
                r_mobile_raw = r_c2.text_input("Mobile Number*", placeholder="e.g. 9876543210")

                r_pass = st.text_input("Create Strong Password*", type="password", placeholder="••••••••")
                strength = check_password_strength(r_pass)
                render_password_checklist(strength)

                f_c1, f_c2 = st.columns(2)
                if f_c1.form_submit_button("Create Business Account", type="primary", use_container_width=True):
                    is_phone_ok, e164_phone, phone_err = validate_international_phone(selected_country, r_mobile_raw)
                    if not all([r_biz.strip(), r_owner.strip(), r_email.strip(), r_pass]):
                        notify("All fields marked with * are required.", "error")
                    elif not is_phone_ok:
                        notify(phone_err, "error")
                    elif not strength["is_valid"]:
                        notify("Please satisfy all password security requirements.", "error")
                    else:
                        ok, msg = register_tenant(r_biz, r_owner, r_email, e164_phone, r_pass)
                        if ok:
                            notify(msg, "success")
                            st.session_state.auth_mode = "LOGIN"
                            st.rerun()
                        else:
                            notify(msg, "error")
                if f_c2.form_submit_button("Back to Homepage", use_container_width=True):
                    st.session_state.auth_mode = "LANDING"
                    st.rerun()
        st.stop()

    h_col1, h_col2 = st.columns([1.2, 1.0], gap="large")
    with h_col1:
        st.markdown("""
        <div class="hero-container">
            <div class="hero-badge">✨ All-In-One SaaS Business Platform</div>
            <div class="hero-title">Run Your Entire Business. <span>Smarter.</span></div>
            <div class="hero-subtitle">
                StockLabel AI brings products, purchases, sales, inventory, billing operations, barcodes, labels, and reports together in one powerful business management platform.
            </div>
        </div>
        """, unsafe_allow_html=True)

        btn_c1, btn_c2 = st.columns([1, 1])
        if btn_c1.button("Create new account", type="primary", use_container_width=True, key="hero_get_started"):
            st.session_state.auth_mode = "REGISTER"
            st.rerun()
        if btn_c2.button("Log In", use_container_width=True, key="hero_signin"):
            st.session_state.auth_mode = "LOGIN"
            st.rerun()

    with h_col2:
        st.markdown("""
        <div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 14px; padding: 18px; box-shadow: 0 10px 30px rgba(0,0,0,0.05);">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #E2E8F0; padding-bottom: 10px; margin-bottom: 12px;">
                <span style="font-weight: 700; color: #1E3A8A; font-size: 0.85rem;">🏢 Enterprise Dashboard Live</span>
                <span style="font-size: 0.75rem; background: #DCFCE7; color: #166534; padding: 2px 8px; border-radius: 10px; font-weight: 700;">● Active Tenant</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; padding: 10px; border-radius: 8px;">
                    <div style="font-size: 0.7rem; color: #64748B; font-weight: 700;">SALES REVENUE</div>
                    <div style="font-size: 1.15rem; font-weight: 800; color: #0F172A;">₹1,48,900.00</div>
                    <div style="font-size: 0.68rem; color: #10B981;">↑ +14.2% this week</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; padding: 10px; border-radius: 8px;">
                    <div style="font-size: 0.7rem; color: #64748B; font-weight: 700;">INVENTORY VALUE</div>
                    <div style="font-size: 1.15rem; font-weight: 800; color: #0F172A;">₹6,85,400.00</div>
                    <div style="font-size: 0.68rem; color: #3B82F6;">540 Units In Stock</div>
                </div>
            </div>
            <div style="background: linear-gradient(135deg, #1E3A8A 0%, #1E40AF 100%); color: white; padding: 12px; border-radius: 8px; margin-bottom: 10px;">
                <div style="font-size: 0.7rem; opacity: 0.85;">TAX INVOICE GENERATOR</div>
                <div style="font-size: 1.1rem; font-weight: 800;">INV-00142 • ₹12,499.00</div>
                <div style="font-size: 0.7rem; opacity: 0.85;">GST & Auto Stock Dispatch Synced</div>
            </div>
            <div style="font-size: 0.75rem; color: #64748B; text-align: center;">🏷️ Code 128 / QR & Thermal Labels Built-in</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""
    <div class="trust-strip">
        <div class="trust-item">✓ Product Management</div>
        <div class="trust-item">✓ Purchase Management</div>
        <div class="trust-item">✓ Sales Management</div>
        <div class="trust-item">✓ Inventory Management</div>
        <div class="trust-item">✓ Barcode Generation</div>
        <div class="trust-item">✓ Label Designer</div>
        <div class="trust-item">✓ Business Reports</div>
        <div class="trust-item">✓ SaaS Architecture</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div id='about'></div>", unsafe_allow_html=True)
    st.markdown("### About StockLabel AI")
    st.write(
        "StockLabel AI brings essential business operations together in one intelligent platform. "
        "From registering products and managing inventory to purchasing stock, generating barcodes, designing labels, creating invoices, and analysing business performance, "
        "StockLabel AI helps businesses manage daily operations with simplicity and complete control."
    )
    st.write(
        "StockLabel AI is a generic, product-based business management platform designed for Retail, Wholesale, Electronics, Fashion, Grocery, Hardware, Manufacturing, General trading, and other product-based businesses."
    )
    st.markdown("---")

    st.markdown("<div id='features'></div>", unsafe_allow_html=True)
    st.markdown("### Powerful Tools. One Platform.")

    f_c1, f_c2, f_c3, f_c4 = st.columns(4)
    with f_c1:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">📦</div>
            <div class="feat-title">Product Management</div>
            <div class="feat-desc">Manage complete catalogs with SKU, barcode, MRP, selling price, GST tax rates, and safety thresholds.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c2:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">🛒</div>
            <div class="feat-title">Purchase Management</div>
            <div class="feat-desc">Manage supplier purchases, creditor balances, inward deliveries, and automatic inventory stock updates.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c3:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">💰</div>
            <div class="feat-title">Sales Management</div>
            <div class="feat-desc">Process customer sales, debtor receivables, tax invoicing, payment statuses, and outward stock dispatches.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c4:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">📊</div>
            <div class="feat-title">Inventory Management</div>
            <div class="feat-desc">Track stock movements, current inventory, low-stock safety alerts, and reorder point indicators.</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top: 16px;'></div>", unsafe_allow_html=True)
    f_c5, f_c6, f_c7, f_c8 = st.columns(4)
    with f_c5:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">🏷️</div>
            <div class="feat-title">Barcode Generation</div>
            <div class="feat-desc">Generate Code 128, EAN-13, EAN-8, UPC-A, Code 39, and QR codes for single items or batch sheets.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c6:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">🎨</div>
            <div class="feat-title">Label Designer</div>
            <div class="feat-desc">Design shelf tags, price stickers, and thermal barcode labels with instant dynamic live preview.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c7:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">📈</div>
            <div class="feat-title">Business Reports</div>
            <div class="feat-desc">Gain insights from Profit & Loss, GST tax obligations, and Customer/Supplier ledger statements.</div>
        </div>
        """, unsafe_allow_html=True)
    with f_c8:
        st.markdown("""
        <div class="feat-card">
            <div class="feat-icon">🏢</div>
            <div class="feat-title">SaaS Architecture</div>
            <div class="feat-desc">Every business operates in its own isolated environment with dedicated database security.</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    st.markdown("<div id='workflow'></div>", unsafe_allow_html=True)
    st.markdown("### How It Works")
    st.caption("Streamlined workflow from cataloging to operational reports.")

    w1, w2, w3, w4, w5 = st.columns(5)
    with w1:
        st.markdown("<div class='flow-badge'><div style='font-weight: 800; color: #1E3A8A;'>Products</div><div style='font-size: 1.2rem; margin: 4px 0;'>↓</div><div style='font-size: 0.8rem;'>Add item catalog</div></div>", unsafe_allow_html=True)
    with w2:
        st.markdown("<div class='flow-badge'><div style='font-weight: 800; color: #1E3A8A;'>Purchase</div><div style='font-size: 1.2rem; margin: 4px 0;'>↓</div><div style='font-size: 0.8rem;'>Inward stock</div></div>", unsafe_allow_html=True)
    with w3:
        st.markdown("<div class='flow-badge'><div style='font-weight: 800; color: #1E3A8A;'>Inventory</div><div style='font-size: 1.2rem; margin: 4px 0;'>↓</div><div style='font-size: 0.8rem;'>Track levels</div></div>", unsafe_allow_html=True)
    with w4:
        st.markdown("<div class='flow-badge'><div style='font-weight: 800; color: #1E3A8A;'>Sales</div><div style='font-size: 1.2rem; margin: 4px 0;'>↓</div><div style='font-size: 0.8rem;'>Invoice customers</div></div>", unsafe_allow_html=True)
    with w5:
        st.markdown("<div class='flow-badge'><div style='font-weight: 800; color: #1E3A8A;'>Reports</div><div style='font-size: 1.2rem; margin: 4px 0;'>✨</div><div style='font-size: 0.8rem;'>Analyze growth</div></div>", unsafe_allow_html=True)

    st.markdown("---")

    ind_col, saas_col = st.columns(2, gap="large")
    with ind_col:
        st.markdown("<div id='industries'></div>", unsafe_allow_html=True)
        st.markdown("### Made for Businesses of Every Size")
        st.write(
            "StockLabel AI is generic and not limited to one particular industry. It can be used for Retail, Wholesale, Electronics, Fashion, Grocery, Hardware, Manufacturing, General trading, and other product-based businesses."
        )

    with saas_col:
        st.markdown("<div id='security'></div>", unsafe_allow_html=True)
        st.markdown("### Secure Multi-Tenant Architecture")
        st.write(
            "Every registered business operates within its own secure environment. Products, inventory, purchases, sales, reports, and settings remain isolated per tenant."
        )

    st.markdown("""
    <div class="cta-banner">
        <h2 style="font-size: 2.2rem; font-weight: 800; margin-bottom: 12px; color: white;">Ready to Simplify Your Business?</h2>
        <p style="font-size: 1.05rem; opacity: 0.9; margin-bottom: 24px; max-width: 650px; margin-left: auto; margin-right: auto;">
            Bring products, inventory, purchases, sales, barcodes, labels, and reports together with StockLabel AI.
        </p>
    </div>
    """, unsafe_allow_html=True)

    cta_btn1, cta_btn2, cta_btn3 = st.columns([1, 1.5, 1])
    with cta_btn2:
        if st.button("🚀 Create Your Business Account Now", type="primary", use_container_width=True, key="cta_bottom_btn"):
            st.session_state.auth_mode = "REGISTER"
            st.rerun()

    st.markdown("""
    <div class="landing-footer">
        <div style="display: flex; justify-content: space-between; flex-wrap: wrap; gap: 20px;">
            <div>
                <div style="font-weight: 800; font-size: 1.2rem; color: #1E3A8A;">🏷️ StockLabel AI</div>
                <div style="color: #64748B; font-size: 0.85rem; margin-top: 4px;">Smart Business Management. Simplified.</div>
            </div>
            <div style="font-size: 0.85rem; color: #475569;">
                <b>Product:</b> Products • Purchase • Sales • Inventory • Barcodes • Reports
            </div>
            <div style="font-size: 0.85rem; color: #475569;">
                <b>Account:</b> Log In • Register
            </div>
        </div>
        <div class="creator-box">
            <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: #64748B; letter-spacing: 1px;">Designed & Developed by</div>
            <div style="font-size: 1.25rem; font-weight: 800; color: #0F172A; margin: 2px 0;">Sidharth Chopra</div>
            <div style="font-size: 0.85rem; color: #1E40AF; font-weight: 600;">14-year-old Creator • Developer • Builder</div>
            <div style="font-size: 0.8rem; color: #64748B; margin-top: 4px;">"Built with curiosity, creativity and a passion for technology."</div>
        </div>
        <div style="text-align: center; color: #94A3B8; font-size: 0.75rem; margin-top: 18px;">
            © 2026 StockLabel AI. All rights reserved.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.stop()


# =====================================================================
# 13. AUTHENTICATED APP: SIDEBAR & NAVIGATION (DYNAMIC NAME SYNC)
# =====================================================================
tenant_info = DatabaseAdapter.execute("SELECT * FROM tenants WHERE id = ?", (st.session_state.tenant_id,), fetch="one") if st.session_state.tenant_id else None
settings = get_tenant_settings()
display_business_name = settings.get("business_name", tenant_info["business_name"] if tenant_info else "StockLabel AI")

with st.sidebar:
    st.markdown(f"### 🏷️ **{display_business_name}**")
    if tenant_info:
        st.caption(f"Plan: **{tenant_info['plan_tier']}** • Status: **{tenant_info['subscription_status']}**")
    else:
        st.caption("🛡️ **System Administrator Portal**")
    st.markdown("---")

    PAGES = [
        "🏠 Dashboard",
        "📦 Products",
        "🛒 Purchase",
        "💰 Sales",
        "💳 Payments",
        "👥 Creditors & Debtors",
        "📊 Inventory Ops",
        "🏷️ Barcodes",
        "🎨 Label Designer",
        "📈 Reports",
        "⚙️ Settings"
    ]

    if st.session_state.user_role == "SUPERADMIN":
        PAGES.append("🛡️ SaaS SuperAdmin")

    if st.session_state.current_page not in PAGES:
        st.session_state.current_page = "🏠 Dashboard"

    selected_page = st.radio("NAVIGATION", PAGES, index=PAGES.index(st.session_state.current_page))
    if selected_page != st.session_state.current_page:
        st.session_state.current_page = selected_page
        st.rerun()

    st.markdown("---")
    if st.session_state.tenant_id:
        p_count_row = DatabaseAdapter.execute("SELECT COUNT(*) as count FROM products WHERE tenant_id = ?", (st.session_state.tenant_id,), fetch="one")
        p_count = p_count_row["count"] if p_count_row else 0
        st.caption(f"Catalog Products: **{p_count}**")
        if p_count == 0:
            if st.button("🚀 Load Sample Data", use_container_width=True):
                seed_tenant_demo_data(st.session_state.tenant_id)
                notify("Sample catalog and accounts loaded successfully.", "success")
                st.rerun()

    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_id = None
        st.session_state.tenant_id = None
        st.session_state.auth_mode = "LANDING"
        notify("Logged out successfully.", "info")
        st.rerun()

menu = st.session_state.current_page


# =====================================================================
# MODULE 1: DASHBOARD
# =====================================================================
if menu == "🏠 Dashboard":
    st.title(f"{display_business_name} — Executive Dashboard")
    st.caption("Live business performance, purchase/sales overviews, and inventory health.")
    st.markdown("---")

    t_id = st.session_state.tenant_id
    today_str = date.today().strftime("%Y-%m-%d")
    current_month_str = date.today().strftime("%Y-%m")

    def render_metric_card(label: str, val_str: str, sub_str: str, is_error: bool = False, is_danger: bool = False) -> str:
        if is_error:
            return f"""
            <div class="metric-card" style="border-left: 4px solid #EF4444;">
                <div class="metric-label">{label}</div>
                <div class="metric-value" style="color: #EF4444; font-size: 1.25rem;">⚠️ Error</div>
                <div class="metric-sub danger">{sub_str}</div>
            </div>
            """
        sub_class = "danger" if is_danger else ""
        return f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{val_str}</div>
            <div class="metric-sub {sub_class}">{sub_str}</div>
        </div>
        """

    try:
        total_p_row = DatabaseAdapter.execute("SELECT COUNT(*) as count FROM products WHERE tenant_id = ?", (t_id,), fetch="one")
        total_products = int(total_p_row["count"]) if total_p_row else 0
        card_1_html = render_metric_card("Total Products", f"{total_products:,}", "Registered catalog items")
    except Exception:
        card_1_html = render_metric_card("Total Products", "", "Error", is_error=True)

    try:
        stock_df = DatabaseAdapter.read_df("""
            SELECT p.purchase_price, p.minimum_stock, IFNULL(i.current_stock, 0) as stock 
            FROM products p LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.tenant_id = ?
        """, [t_id])
        total_stock = stock_df["stock"].sum() if not stock_df.empty else 0.0
        inventory_val = (stock_df["stock"] * stock_df["purchase_price"]).sum() if not stock_df.empty else 0.0
        low_stock_df = stock_df[stock_df["stock"] <= stock_df["minimum_stock"]] if not stock_df.empty else pd.DataFrame()
        low_stock_count = low_stock_df.shape[0]

        card_2_html = render_metric_card("Current Stock", f"{total_stock:,.1f}", f"{low_stock_count} item(s) below threshold", is_danger=(low_stock_count > 0))
        card_3_html = render_metric_card("Inventory Value", format_currency(inventory_val), "At purchase cost basis")
    except Exception:
        card_2_html = render_metric_card("Current Stock", "", "Error", is_error=True)
        card_3_html = render_metric_card("Inventory Value", "", "Error", is_error=True)

    try:
        sales_today_row = DatabaseAdapter.execute("SELECT SUM(grand_total) as total FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND invoice_date = ? AND status = 'Active'", (t_id, today_str), fetch="one")
        today_sales = float(sales_today_row["total"]) if sales_today_row and sales_today_row["total"] else 0.0

        purch_today_row = DatabaseAdapter.execute("SELECT SUM(grand_total) as total FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND invoice_date = ? AND status = 'Active'", (t_id, today_str), fetch="one")
        today_purch = float(purch_today_row["total"]) if purch_today_row and purch_today_row["total"] else 0.0

        deb_row = DatabaseAdapter.execute("SELECT SUM(balance) as total FROM debitor_accounts WHERE tenant_id = ?", (t_id,), fetch="one")
        deb_due = float(deb_row["total"]) if deb_row and deb_row["total"] else 0.0

        cred_row = DatabaseAdapter.execute("SELECT SUM(balance) as total FROM creditor_accounts WHERE tenant_id = ?", (t_id,), fetch="one")
        cred_due = float(cred_row["total"]) if cred_row and cred_row["total"] else 0.0

        card_4_html = render_metric_card("Today's Sales", format_currency(today_sales), "Billed revenue")
        card_5_html = render_metric_card("Today's Purchases", format_currency(today_purch), "Inward orders")
        card_6_html = render_metric_card("Debitor Receivables", format_currency(deb_due), "Customer dues")
        card_7_html = render_metric_card("Creditor Payables", format_currency(cred_due), "Supplier dues", is_danger=(cred_due > 0))
    except Exception:
        card_4_html = card_5_html = card_6_html = card_7_html = render_metric_card("Metric", "", "Error", is_error=True)

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.markdown(card_1_html, unsafe_allow_html=True)
    mc2.markdown(card_2_html, unsafe_allow_html=True)
    mc3.markdown(card_3_html, unsafe_allow_html=True)
    mc4.markdown(card_4_html, unsafe_allow_html=True)

    mc5, mc6, mc7, mc8 = st.columns(4)
    mc5.markdown(card_5_html, unsafe_allow_html=True)
    mc6.markdown(card_6_html, unsafe_allow_html=True)
    mc7.markdown(card_7_html, unsafe_allow_html=True)
    mc8.markdown(render_metric_card("Inventory Health", "Healthy" if low_stock_count == 0 else f"{low_stock_count} Alert(s)", "Safety stock limits", is_danger=(low_stock_count > 0)), unsafe_allow_html=True)

    st.markdown("---")

    p_col, s_col = st.columns(2, gap="large")

    with p_col:
        st.subheader("🛒 Purchase Overview")
        try:
            m_purch_row = DatabaseAdapter.execute("SELECT SUM(grand_total) as total, COUNT(*) as cnt FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND invoice_date LIKE ? AND status = 'Active'", (t_id, f"{current_month_str}%"), fetch="one")
            m_purch_tot = float(m_purch_row["total"]) if m_purch_row and m_purch_row["total"] else 0.0
            m_purch_cnt = int(m_purch_row["cnt"]) if m_purch_row and m_purch_row["cnt"] else 0

            p_ov1, p_ov2 = st.columns(2)
            p_ov1.metric("This Month's Purchases", format_currency(m_purch_tot))
            p_ov2.metric("Purchase Invoices", f"{m_purch_cnt}")

            recent_purchases = DatabaseAdapter.read_df("SELECT invoice_number, invoice_date, party_name, grand_total, payment_status FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND status = 'Active' ORDER BY id DESC LIMIT 4", [t_id])
            st.dataframe(recent_purchases, use_container_width=True, hide_index=True)
            if st.button("View All Purchases ➔", key="btn_view_purch"):
                navigate_to("🛒 Purchase")
        except Exception:
            st.error("Unable to load purchase overview.")

    with s_col:
        st.subheader("💰 Sales Overview")
        try:
            m_sales_row = DatabaseAdapter.execute("SELECT SUM(grand_total) as total, COUNT(*) as cnt FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND invoice_date LIKE ? AND status = 'Active'", (t_id, f"{current_month_str}%"), fetch="one")
            m_sales_tot = float(m_sales_row["total"]) if m_sales_row and m_sales_row["total"] else 0.0
            m_sales_cnt = int(m_sales_row["cnt"]) if m_sales_row and m_sales_row["cnt"] else 0

            s_ov1, s_ov2 = st.columns(2)
            s_ov1.metric("This Month's Sales", format_currency(m_sales_tot))
            s_ov2.metric("Sales Invoices", f"{m_sales_cnt}")

            recent_sales = DatabaseAdapter.read_df("SELECT invoice_number, invoice_date, party_name, grand_total, payment_status FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND status = 'Active' ORDER BY id DESC LIMIT 4", [t_id])
            st.dataframe(recent_sales, use_container_width=True, hide_index=True)
            if st.button("View All Sales ➔", key="btn_view_sales"):
                navigate_to("💰 Sales")
        except Exception:
            st.error("Unable to load sales overview.")

    st.markdown("---")

    st.subheader("⚠️ Inventory Health & Low Stock Inspection")
    try:
        low_stock_details = DatabaseAdapter.read_df("""
            SELECT p.id, p.name, p.sku, p.category, p.unit, p.minimum_stock, IFNULL(i.current_stock, 0) as current_stock
            FROM products p LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.tenant_id = ? AND IFNULL(i.current_stock, 0) <= p.minimum_stock
            ORDER BY current_stock ASC
        """, [t_id])

        if not low_stock_details.empty:
            st.warning(f"Found {len(low_stock_details)} product(s) at or below safety stock threshold!")
            st.dataframe(
                low_stock_details,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "current_stock": st.column_config.NumberColumn("Current Stock", format="%.1f"),
                    "minimum_stock": st.column_config.NumberColumn("Safety Stock Limit", format="%d")
                }
            )
        else:
            st.success("✅ All products are currently above their safety stock thresholds. Inventory is healthy.")
    except Exception:
        st.error("Unable to evaluate inventory health status.")


# =====================================================================
# MODULE 2: PRODUCTS
# =====================================================================
elif menu == "📦 Products":
    st.title("Products & Catalog")
    st.caption("Add, manage, and inspect your business products and pricing tiers.")

    t_id = st.session_state.tenant_id
    settings = get_tenant_settings()

    tab_list, tab_add, tab_clear = st.tabs(["📋 Product Directory", "➕ Add New Product", "🗑️ Clear All Products"])

    with tab_list:
        f1, f2, f3 = st.columns([3, 1.5, 1.5])
        search_kw = f1.text_input("🔍 Search Catalog", placeholder="Search by name, SKU, or barcode...")
        cats_res = DatabaseAdapter.execute("SELECT name FROM categories WHERE tenant_id = ?", (t_id,), fetch="all")
        cats = [row["name"] for row in cats_res] if cats_res else []
        cat_filter = f2.selectbox("Category Filter", ["All Categories"] + cats)
        stat_filter = f3.selectbox("Status", ["All", "Active", "Inactive"])

        query = """
            SELECT p.id, p.name, p.sku, p.barcode, p.category, p.brand, p.unit,
                   p.purchase_price, p.mrp, p.selling_price, p.gst_rate, p.hsn_sac,
                   IFNULL(i.current_stock, 0) as current_stock, p.status
            FROM products p
            LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.tenant_id = ?
        """
        params = [t_id]
        if search_kw:
            query += " AND (p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ?)"
            params.extend([f"%{search_kw}%", f"%{search_kw}%", f"%{search_kw}%"])
        if cat_filter != "All Categories":
            query += " AND p.category = ?"
            params.append(cat_filter)
        if stat_filter != "All":
            query += " AND p.status = ?"
            params.append(stat_filter)

        query += " ORDER BY p.id DESC"
        p_df = DatabaseAdapter.read_df(query, params)

        display_df = p_df.copy()
        for col in ["category", "brand", "unit", "hsn_sac"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].fillna("—").replace("", "—")

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("Product ID", format="%d"),
                "purchase_price": st.column_config.NumberColumn("Purchase Price", format="₹%.2f"),
                "mrp": st.column_config.NumberColumn("MRP", format="₹%.2f"),
                "selling_price": st.column_config.NumberColumn("Selling Price", format="₹%.2f"),
                "gst_rate": st.column_config.NumberColumn("GST %", format="%.1f%%"),
                "current_stock": st.column_config.NumberColumn("In Stock", format="%.1f")
            }
        )

        if not p_df.empty:
            st.markdown("---")
            st.markdown("##### ✏️ Manage Selected Product")
            sel_pid = st.selectbox(
                "Choose Product to Edit or Delete",
                p_df["id"].tolist(),
                format_func=lambda x: f"ID {x} — {p_df[p_df['id'] == x]['name'].values[0]} ({p_df[p_df['id'] == x]['sku'].values[0]})"
            )

            p_data = DatabaseAdapter.execute("SELECT * FROM products WHERE id = ? AND tenant_id = ?", (sel_pid, t_id), fetch="one")
            if p_data:
                with st.expander(f"Edit Details for '{p_data['name']}' (Product ID: {p_data['id']})", expanded=True):
                    with st.form(f"edit_p_form_{sel_pid}"):
                        st.caption("Complete any missing or blank product attributes below.")
                        e1, e2, e3 = st.columns(3)
                        e_name = e1.text_input("Product Name*", p_data["name"])
                        e_sku = e2.text_input("SKU Code*", p_data["sku"])
                        e_barcode = e3.text_input("Barcode*", p_data["barcode"])

                        e4, e5, e6 = st.columns(3)
                        e_cat = e4.selectbox("Category", cats if cats else ["General"], index=cats.index(p_data["category"]) if p_data["category"] in cats else 0)
                        e_brand = e5.text_input("Brand", p_data["brand"] or "")
                        units_res = DatabaseAdapter.execute("SELECT name FROM units WHERE tenant_id = ?", (t_id,), fetch="all")
                        all_units = [u["name"] for u in units_res] if units_res else ["Piece"]
                        e_unit = e6.selectbox("Unit", all_units, index=all_units.index(p_data["unit"]) if p_data["unit"] in all_units else 0)

                        e7, e8, e9, e10 = st.columns(4)
                        e_pur = e7.number_input("Purchase Price (₹)", min_value=0.0, value=float(p_data["purchase_price"] or 0.0), step=10.0)
                        e_mrp = e8.number_input("MRP (₹)", min_value=0.0, value=float(p_data["mrp"] or 0.0), step=10.0)
                        e_sel = e9.number_input("Selling Price (₹)", min_value=0.0, value=float(p_data["selling_price"] or 0.0), step=10.0)
                        e_gst = e10.number_input("GST %", min_value=0.0, max_value=100.0, value=float(p_data["gst_rate"] or 18.0), step=1.0)

                        with st.expander("More Details (HSN, Stock Limits, Status)"):
                            em1, em2, em3 = st.columns(3)
                            e_hsn = em1.text_input("HSN / SAC Code", p_data["hsn_sac"] or "")
                            e_min = em2.number_input("Safety Stock Limit", min_value=0, value=int(p_data["minimum_stock"] or 5))
                            e_stat = em3.selectbox("Status", ["Active", "Inactive"], index=0 if p_data["status"] == "Active" else 1)

                        if st.form_submit_button("💾 Update Product Record", type="primary"):
                            try:
                                DatabaseAdapter.execute("""
                                    UPDATE products SET
                                        name = ?, sku = ?, barcode = ?, category = ?, brand = ?,
                                        unit = ?, purchase_price = ?, mrp = ?, selling_price = ?,
                                        gst_rate = ?, hsn_sac = ?, minimum_stock = ?, status = ?
                                    WHERE id = ? AND tenant_id = ?
                                """, (e_name, e_sku, e_barcode, e_cat, e_brand, e_unit, e_pur, e_mrp, e_sel, e_gst, e_hsn, e_min, e_stat, sel_pid, t_id))
                                log_tenant_audit("PRODUCT_UPDATE", f"Updated product ID {sel_pid}: {e_name}")
                                notify("Product updated successfully.", "success")
                                st.rerun()
                            except Exception as err:
                                notify(format_error(err), "error")

                with st.expander("🗑️ Delete this Product Record"):
                    st.warning(f"**Delete Product: {p_data['name']} (ID: {p_data['id']})?**\nThis product will be removed from your catalog. Historical transactions remain intact.")
                    confirm_del = st.checkbox("Yes, I understand and want to delete this product.", key=f"conf_del_{sel_pid}")
                    if st.button("Delete Product Permanently", type="primary", disabled=not confirm_del, key=f"btn_del_{sel_pid}"):
                        try:
                            DatabaseAdapter.execute("DELETE FROM products WHERE id = ? AND tenant_id = ?", (sel_pid, t_id))
                            log_tenant_audit("PRODUCT_DELETE", f"Deleted product ID {sel_pid}")
                            notify("Product deleted successfully.", "success")
                            st.rerun()
                        except Exception as err:
                            notify(format_error(err), "error")

    with tab_add:
        next_id = get_next_tenant_product_id()
        auto_sku = f"{settings.get('sku_prefix', 'PRD-')}{next_id:05d}"
        auto_barcode = f"890{t_id:03d}{next_id:06d}"

        st.markdown(f"#### ➕ Add Product (Permanent ID: **{next_id}**)")
        st.caption("Fill the basic info below to register a product.")

        with st.form("add_product_form", clear_on_submit=True):
            st.markdown('<div class="section-head">📦 1. Basic Information</div>', unsafe_allow_html=True)
            b1, b2, b3 = st.columns(3)
            p_name = b1.text_input("Product Name*", placeholder="e.g. Wireless Mouse")
            units_res = DatabaseAdapter.execute("SELECT name FROM units WHERE tenant_id = ?", (t_id,), fetch="all")
            all_units = [u["name"] for u in units_res] if units_res else ["Piece"]
            p_unit = b2.selectbox("Unit of Measure", all_units, index=0)
            p_cat = b3.selectbox("Category", cats if cats else ["General"])

            st.markdown('<div class="section-head">💰 2. Pricing & Taxes</div>', unsafe_allow_html=True)
            pr1, pr2, pr3, pr4 = st.columns(4)
            p_pur = pr1.number_input("Purchase Price (₹)*", min_value=0.0, value=100.0, step=10.0)
            p_mrp = pr2.number_input("MRP (₹)", min_value=0.0, value=199.0, step=10.0)
            p_sel = pr3.number_input("Selling Price (₹)*", min_value=0.0, value=149.0, step=10.0)
            p_gst = pr4.number_input("GST Rate %", min_value=0.0, max_value=100.0, value=float(settings.get("default_gst", 18.0)), step=1.0)

            st.markdown('<div class="section-head">📊 3. Stock & Identifiers</div>', unsafe_allow_html=True)
            s1, s2, s3, s4 = st.columns(4)
            p_open_stock = s1.number_input("Opening Stock Qty", min_value=0.0, value=10.0, step=1.0)
            p_min_stock = s2.number_input("Safety Stock Alert", min_value=0, value=int(settings.get("low_stock_threshold", 5)))
            p_sku = s3.text_input("SKU Code*", value=auto_sku)
            p_barcode = s4.text_input("Barcode Number*", value=auto_barcode)

            if st.form_submit_button("✅ Save Product", type="primary", use_container_width=True):
                if not p_name.strip():
                    notify("Please provide a valid Product Name.", "error")
                else:
                    try:
                        new_pid = DatabaseAdapter.execute("""
                            INSERT INTO products (tenant_id, name, sku, barcode, category, brand, description, unit,
                                                  purchase_price, mrp, selling_price, gst_rate, hsn_sac, minimum_stock)
                            VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, '', ?)
                        """, (t_id, p_name.strip(), p_sku.strip(), p_barcode.strip(), p_cat, p_unit, p_pur, p_mrp, p_sel, p_gst, p_min_stock), fetch="lastrowid")

                        DatabaseAdapter.execute("INSERT INTO inventory (product_id, tenant_id, current_stock) VALUES (?, ?, ?)", (new_pid, t_id, p_open_stock))
                        if p_open_stock > 0:
                            DatabaseAdapter.execute("""
                                INSERT INTO inventory_movements (tenant_id, product_id, operation, quantity, stock_before, stock_after, reference, notes)
                                VALUES (?, ?, 'Opening Stock', ?, 0, ?, 'INITIAL', 'Initial stock assignment')
                            """, (t_id, new_pid, p_open_stock, p_open_stock))

                        log_tenant_audit("PRODUCT_CREATE", f"Created product ID {new_pid}: {p_name}")
                        notify("Product created successfully.", "success")
                        st.rerun()
                    except Exception as err:
                        notify(format_error(err), "error")

    with tab_clear:
        st.markdown("#### 🗑️ Clear All Products")
        st.warning("⚠️ **WARNING:** You are about to permanently delete all products and inventory in your catalog for this business.")
        clear_conf_text = st.text_input("Type 'CLEAR ALL' to confirm product deletion", key="clear_prod_text")
        if st.button("Confirm Clear All Products", type="primary"):
            if clear_conf_text.strip() == "CLEAR ALL":
                DatabaseAdapter.execute("DELETE FROM products WHERE tenant_id = ?", (t_id,))
                log_tenant_audit("CLEAR_ALL_PRODUCTS", "Cleared all products for tenant.")
                notify("All product entries have been cleared successfully.", "success")
                st.rerun()
            else:
                notify("Confirmation text did not match 'CLEAR ALL'.", "error")


# =====================================================================
# MODULE 3: PURCHASE MANAGEMENT
# =====================================================================
elif menu == "🛒 Purchase":
    st.title("Purchase Management")
    st.caption("Manage supplier purchase orders, creditor liabilities, and inward inventory synchronization.")

    t_id = st.session_state.tenant_id
    settings = get_tenant_settings()

    purch_tab1, purch_tab2, purch_tab3 = st.tabs(["🛒 New Purchase Order", "📜 Purchase History", "🗑️ Clear All Purchases"])

    with purch_tab1:
        with st.expander("➕ Register New Supplier / Creditor"):
            with st.form("quick_add_creditor"):
                qp1, qp2 = st.columns(2)
                q_name = qp1.text_input("Business Name*")
                q_gst = qp2.text_input("GSTIN (e.g. 01ABCDE1234F1Z5)")
                qp3, qp4 = st.columns(2)
                q_country = qp3.selectbox("Country*", list(COUNTRY_CALLING_CODES.keys()), index=0, key="sup_country")
                q_phone = qp4.text_input("Mobile Number*")
                q_addr = st.text_input("Address")
                q_email = st.text_input("Email")

                if st.form_submit_button("Save Supplier Account", type="primary"):
                    is_p_ok, e164_p, p_err = validate_international_phone(q_country, q_phone)
                    gst_ok = validate_gstin(q_gst)
                    if not q_name.strip():
                        notify("Business Name is required.", "error")
                    elif not is_p_ok:
                        notify(p_err, "error")
                    elif not gst_ok:
                        notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                    else:
                        try:
                            DatabaseAdapter.execute("""
                                INSERT INTO creditor_accounts (tenant_id, name, business_name, phone, gstin, address, email, balance)
                                VALUES (?, ?, '', ?, ?, ?, ?, 0)
                                ON CONFLICT DO NOTHING
                            """, (t_id, q_name.strip(), e164_p, q_gst.strip().upper(), q_addr.strip(), q_email.strip()))
                            notify("Supplier added successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")

        h1, h2 = st.columns(2)
        last_inv_row = DatabaseAdapter.execute("SELECT COUNT(*) as count FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE'", (t_id,), fetch="one")
        last_inv = last_inv_row["count"] if last_inv_row else 0
        gen_inv_no = f"{settings.get('purchase_prefix', 'PUR-')}{last_inv + 1:05d}"

        invoice_num = h1.text_input("Invoice Number*", value=gen_inv_no)
        invoice_date_val = h2.date_input("Invoice Date", value=date.today())

        st.markdown("##### 🏢 Supplier Details & Autocomplete")
        creditors = DatabaseAdapter.execute("SELECT * FROM creditor_accounts WHERE tenant_id = ?", (t_id,), fetch="all") or []

        sup_query = st.text_input("Search Supplier / Creditor (Type 2+ letters to autocomplete)", placeholder="e.g. Ra for Raj Traders")

        matched_suppliers = []
        if sup_query and len(sup_query.strip()) >= 2:
            matched_suppliers = [c for c in creditors if sup_query.strip().lower() in c["name"].lower()]

        selected_auto_sup = None
        if matched_suppliers:
            sup_labels = {c["name"]: f"{c['name']} (GSTIN: {c['gstin'] or 'N/A'})" for c in matched_suppliers}
            chosen_sup_label = st.selectbox("Matching Suppliers Found", list(sup_labels.values()))
            selected_auto_sup = next(c for c in matched_suppliers if sup_labels[c["name"]] == chosen_sup_label)

        default_s_name = selected_auto_sup["name"] if selected_auto_sup else ""
        default_s_phone = selected_auto_sup["phone"] if selected_auto_sup else ""
        default_s_gst = selected_auto_sup["gstin"] if selected_auto_sup else ""

        p1, p2, p3 = st.columns(3)
        party_name_in = p1.text_input("Business Name*", value=default_s_name, placeholder="e.g. Apex Distributors")
        party_phone_in = p2.text_input("Mobile Number*", value=default_s_phone, placeholder="e.g. +91 98000 00000")
        party_gstin_in = p3.text_input("Supplier GSTIN", value=default_s_gst, placeholder="e.g. 27ABCDE1234F1Z1")

        st.markdown("---")

        st.markdown("##### ➕ Add Item to Purchase")
        with st.form("add_purchase_item_form", clear_on_submit=True):
            f1, f2, f3, f4 = st.columns([2.5, 1.2, 1.2, 1.2])
            item_name_val = f1.text_input("Item Name*", placeholder="e.g. Wireless Mouse")
            item_qty_val = f2.number_input("Quantity*", min_value=0.1, value=1.0, step=1.0)
            item_price_val = f3.number_input("Item Price (₹)*", min_value=0.0, value=100.0, step=10.0)
            item_gst_val = f4.number_input("GST %", min_value=0.0, max_value=100.0, value=18.0, step=1.0)

            calc_base = item_qty_val * item_price_val
            calc_gst_amt = calc_base * (item_gst_val / 100.0)
            calc_total = calc_base + calc_gst_amt
            st.info(f"Total Amount (Auto-Calculated): ₹{calc_total:,.2f}")

            if st.form_submit_button("➕ Add Item", type="primary"):
                if not item_name_val.strip():
                    notify("Please enter the item name.", "error")
                elif item_qty_val <= 0:
                    notify("Please enter a valid quantity.", "error")
                else:
                    st.session_state.purchase_items.append({
                        "item_name": item_name_val.strip(),
                        "quantity": item_qty_val,
                        "item_price": item_price_val,
                        "gst_rate": item_gst_val,
                        "total_amount": calc_total
                    })
                    notify("Item added successfully.", "success")
                    st.rerun()

        if st.session_state.purchase_items:
            st.markdown("##### 🛒 Purchase Items")
            calc_rows = []
            overall_purchase_total = 0.0

            for idx, itm in enumerate(st.session_state.purchase_items):
                base_amt = itm["item_price"] * itm["quantity"]
                gst_amt = base_amt * (itm["gst_rate"] / 100.0)
                line_total = base_amt + gst_amt
                overall_purchase_total += line_total

                calc_rows.append({
                    "#": idx + 1,
                    "Item Name": itm["item_name"],
                    "Quantity": itm["quantity"],
                    "Item Price": format_currency(itm["item_price"]),
                    "GST %": f"{itm['gst_rate']}%",
                    "Total Amount": format_currency(line_total)
                })

            st.dataframe(pd.DataFrame(calc_rows), use_container_width=True, hide_index=True)

            if st.button("🗑️ Clear Items"):
                st.session_state.purchase_items = []
                st.rerun()

            st.markdown("---")
            sb1, sb2 = st.columns([1, 1.2])
            with sb1:
                st.markdown(f"""
                <div class="total-box">
                    <div class="total-box-label">PURCHASE TOTAL</div>
                    <div class="total-box-amount">{format_currency(overall_purchase_total)}</div>
                </div>
                """, unsafe_allow_html=True)

            with sb2:
                pay_status = st.selectbox("Payment Status", ["Paid", "Partially Paid", "Unpaid"])
                paid_amt = st.number_input("Amount Paid to Supplier (₹)", min_value=0.0, max_value=float(overall_purchase_total), value=float(overall_purchase_total) if pay_status == "Paid" else 0.0)
                pay_method = st.selectbox("Payment Mode", ["Bank Transfer", "Cheque", "Cash", "UPI", "Other"])
                outstanding_amt = overall_purchase_total - paid_amt
                st.caption(f"Creditor Liability Balance: **{format_currency(outstanding_amt)}**")

            if st.button("🚀 Save Purchase", type="primary", use_container_width=True):
                gstin_valid = validate_gstin(party_gstin_in)
                if not party_name_in.strip():
                    notify("Business Name is required.", "error")
                elif not party_phone_in.strip():
                    notify("Mobile Number is required.", "error")
                elif not gstin_valid:
                    notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                else:
                    try:
                        DatabaseAdapter.execute("""
                            INSERT INTO creditor_accounts (tenant_id, name, business_name, phone, gstin, balance) 
                            VALUES (?, ?, '', ?, ?, 0)
                            ON CONFLICT DO NOTHING
                        """, (t_id, party_name_in.strip(), party_phone_in.strip(), party_gstin_in.strip().upper()))
                        DatabaseAdapter.execute("UPDATE creditor_accounts SET balance = balance + ? WHERE tenant_id = ? AND name = ?", (outstanding_amt, t_id, party_name_in.strip()))

                        total_subtotal = sum(i["item_price"] * i["quantity"] for i in st.session_state.purchase_items)
                        total_gst = sum((i["item_price"] * i["quantity"]) * (i["gst_rate"] / 100.0) for i in st.session_state.purchase_items)

                        tx_id = DatabaseAdapter.execute("""
                            INSERT INTO billing_transactions (
                                tenant_id, invoice_number, transaction_type, party_type, party_name, business_name,
                                phone, gstin, invoice_date, due_date, subtotal, gst_total, grand_total,
                                paid_amount, outstanding_amount, payment_status, payment_method, status
                            ) VALUES (?, ?, 'PURCHASE', 'CREDITOR', ?, '', ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 'Active')
                        """, (
                            t_id, invoice_num, party_name_in.strip(), party_phone_in.strip(), party_gstin_in.strip().upper(),
                            invoice_date_val.strftime("%Y-%m-%d"),
                            total_subtotal, total_gst, overall_purchase_total, paid_amt, outstanding_amt, pay_status, pay_method
                        ), fetch="lastrowid")

                        for itm in st.session_state.purchase_items:
                            auto_pid = get_or_create_product_from_purchase(t_id, itm["item_name"], itm["item_price"], itm["gst_rate"])

                            taxable = itm["item_price"] * itm["quantity"]
                            gst_amt = taxable * (itm["gst_rate"] / 100.0)
                            line_tot = taxable + gst_amt

                            DatabaseAdapter.execute("""
                                INSERT INTO billing_items (
                                    tenant_id, transaction_id, product_id, item_name, item_price, mrp, selling_price,
                                    quantity, gst_rate, gst_amount, total_amount
                                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?)
                            """, (t_id, tx_id, auto_pid, itm["item_name"], itm["item_price"], itm["quantity"], itm["gst_rate"], gst_amt, line_tot))

                            adjust_inventory_stock(auto_pid, itm["quantity"], "Purchase Inward", invoice_num, f"Purchased from {party_name_in}")

                        update_party_ledger("CREDITOR", party_name_in.strip(), f"Purchase Invoice {invoice_num}", debit=paid_amt, credit=overall_purchase_total, reference=invoice_num)
                        log_tenant_audit("PURCHASE_CREATED", f"Recorded purchase order {invoice_num} from {party_name_in}")
                        st.session_state.purchase_items = []
                        notify("Purchase recorded successfully.", "success")
                        st.rerun()
                    except Exception as err:
                        notify(format_error(err), "error")
        else:
            st.info("Add items to your purchase order above.")

    with purch_tab2:
        st.markdown("#### 📜 Purchase History & PDF Invoices")
        purch_df = DatabaseAdapter.read_df("SELECT * FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' ORDER BY id DESC", [t_id])
        st.dataframe(purch_df[["id", "invoice_number", "invoice_date", "party_name", "grand_total", "paid_amount", "outstanding_amount", "payment_status", "status"]] if not purch_df.empty else purch_df, use_container_width=True, hide_index=True)

        if not purch_df.empty:
            sel_pid_p = st.selectbox("Select Purchase Invoice", purch_df["id"].tolist(), format_func=lambda x: f"{purch_df[purch_df['id'] == x]['invoice_number'].values[0]} — {purch_df[purch_df['id'] == x]['party_name'].values[0]}")
            tx_row = DatabaseAdapter.execute("SELECT * FROM billing_transactions WHERE id = ? AND tenant_id = ?", (sel_pid_p, t_id), fetch="one")
            items_row = DatabaseAdapter.execute("SELECT * FROM billing_items WHERE transaction_id = ? AND tenant_id = ?", (sel_pid_p, t_id), fetch="all") or []
            if tx_row:
                pdf_bytes = generate_invoice_pdf(dict(tx_row), [dict(r) for r in items_row], settings)
                st.download_button("📥 Download Purchase Invoice PDF", data=pdf_bytes, file_name=f"{tx_row['invoice_number']}.pdf", mime="application/pdf", type="primary")

                st.markdown("---")
                with st.expander("🗑️ Delete Purchase Record"):
                    st.warning("⚠️ Deleting this purchase will reverse the inventory stock increase and recalculate creditor balances.")
                    conf_del_p = st.checkbox("Confirm purchase deletion", key=f"conf_del_p_{sel_pid_p}")
                    if st.button("Delete Purchase Permanently", type="primary", disabled=not conf_del_p):
                        try:
                            for itm in items_row:
                                if itm["product_id"]:
                                    adjust_inventory_stock(itm["product_id"], -itm["quantity"], "Purchase Deletion", tx_row["invoice_number"], "Reversed due to purchase deletion")
                            DatabaseAdapter.execute("UPDATE creditor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (tx_row["outstanding_amount"], t_id, tx_row["party_name"]))
                            DatabaseAdapter.execute("DELETE FROM billing_transactions WHERE id = ? AND tenant_id = ?", (sel_pid_p, t_id))
                            log_tenant_audit("PURCHASE_DELETE", f"Deleted purchase invoice {tx_row['invoice_number']}")
                            notify("Purchase deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")

    with purch_tab3:
        st.markdown("#### 🗑️ Clear All Purchases")
        st.warning("⚠️ **WARNING:** You are about to permanently clear all purchase transactions for this business.")
        clear_conf_purch = st.text_input("Type 'CLEAR ALL' to confirm purchase clearance", key="clear_purch_text")
        if st.button("Confirm Clear All Purchases", type="primary"):
            if clear_conf_purch.strip() == "CLEAR ALL":
                try:
                    p_txs = DatabaseAdapter.execute("SELECT id FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE'", (t_id,), fetch="all") or []
                    for pt in p_txs:
                        items_row = DatabaseAdapter.execute("SELECT * FROM billing_items WHERE transaction_id = ? AND tenant_id = ?", (pt["id"], t_id), fetch="all") or []
                        tx_row = DatabaseAdapter.execute("SELECT * FROM billing_transactions WHERE id = ? AND tenant_id = ?", (pt["id"], t_id), fetch="one")
                        for itm in items_row:
                            if itm["product_id"]:
                                adjust_inventory_stock(itm["product_id"], -itm["quantity"], "Purchase Deletion", tx_row["invoice_number"], "Reversed due to bulk clearance")
                        if tx_row:
                            DatabaseAdapter.execute("UPDATE creditor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (tx_row["outstanding_amount"], t_id, tx_row["party_name"]))

                    DatabaseAdapter.execute("DELETE FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE'", (t_id,))
                    log_tenant_audit("CLEAR_ALL_PURCHASES", "Cleared all purchase records for tenant.")
                    notify("All purchase entries have been cleared successfully.", "success")
                    st.rerun()
                except Exception as e:
                    notify(format_error(e), "error")
            else:
                notify("Confirmation text did not match 'CLEAR ALL'.", "error")


# =====================================================================
# MODULE 4: SALES MANAGEMENT
# =====================================================================
elif menu == "💰 Sales":
    st.title("Sales Management")
    st.caption("Process customer sales orders, debtor receivables, tax invoicing, and outward stock dispatches.")

    t_id = st.session_state.tenant_id
    settings = get_tenant_settings()

    sales_tab1, sales_tab2, sales_tab3 = st.tabs(["🛒 New Sales Invoice", "📜 Sales History", "🗑️ Clear All Sales"])

    with sales_tab1:
        with st.expander("➕ Register New Customer / Debitor"):
            with st.form("quick_add_debitor"):
                qp1, qp2 = st.columns(2)
                q_name = qp1.text_input("Business Name*")
                q_biz = qp2.text_input("Business Sub-Name")
                qp3, qp4 = st.columns(2)
                q_country = qp3.selectbox("Country*", list(COUNTRY_CALLING_CODES.keys()), index=0, key="cust_country")
                q_phone = qp4.text_input("Mobile Number*")
                q_gst = qp4.text_input("GSTIN (e.g. 01ABCDE1234F1Z5)")

                if st.form_submit_button("Save Customer Account", type="primary"):
                    is_c_ok, e164_c, c_err = validate_international_phone(q_country, q_phone)
                    gst_ok = validate_gstin(q_gst)
                    if not q_name.strip():
                        notify("Business Name is required.", "error")
                    elif not is_c_ok:
                        notify(c_err, "error")
                    elif not gst_ok:
                        notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                    else:
                        DatabaseAdapter.execute("""
                            INSERT INTO debitor_accounts (tenant_id, name, business_name, phone, gstin, balance)
                            VALUES (?, ?, ?, ?, ?, 0)
                            ON CONFLICT DO NOTHING
                        """, (t_id, q_name.strip(), q_biz.strip(), e164_c, q_gst.strip().upper()))
                        notify("Customer added successfully.", "success")
                        st.rerun()

        h1, h2, h3 = st.columns(3)
        last_inv_row = DatabaseAdapter.execute("SELECT COUNT(*) as count FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE'", (t_id,), fetch="one")
        last_inv = last_inv_row["count"] if last_inv_row else 0
        gen_inv_no = f"{settings.get('sales_prefix', 'INV-')}{last_inv + 1:05d}"

        invoice_num = h1.text_input("Invoice Number*", value=gen_inv_no)
        invoice_date_val = h2.date_input("Invoice Date", value=date.today())
        due_date_val = h3.date_input("Due Date", value=date.today() + timedelta(days=15))

        p1, p2, p3, p4 = st.columns(4)
        party_name_in = p1.text_input("Business Name*", placeholder="e.g. Metro Retail Hub")
        party_biz_in = p2.text_input("Business Sub-Name", placeholder="e.g. Metro Stores")
        party_phone_in = p3.text_input("Mobile Number*", placeholder="e.g. +91 98000 00000")
        party_gstin_in = p4.text_input("Customer GSTIN", placeholder="e.g. 27XYZAB9876C1Z2")

        st.markdown("---")

        st.markdown("##### ➕ Add Item to Sale")
        with st.form("add_sales_item_form", clear_on_submit=True):
            f1, f2, f3, f4 = st.columns([2.5, 1.2, 1.2, 1.2])
            item_name_val = f1.text_input("Item Name*", placeholder="e.g. Wireless Mouse")
            item_qty_val = f2.number_input("Quantity*", min_value=0.1, value=1.0, step=1.0)
            item_price_val = f3.number_input("Item Price (₹)*", min_value=0.0, value=150.0, step=10.0)
            item_gst_val = f4.number_input("GST %", min_value=0.0, max_value=100.0, value=18.0, step=1.0)

            calc_base = item_qty_val * item_price_val
            calc_gst_amt = calc_base * (item_gst_val / 100.0)
            calc_total = calc_base + calc_gst_amt
            st.info(f"Total Amount (Auto-Calculated): ₹{calc_total:,.2f}")

            if st.form_submit_button("➕ Add Item", type="primary"):
                if not item_name_val.strip():
                    notify("Item Name cannot be blank.", "error")
                elif item_qty_val <= 0:
                    notify("Please enter a valid quantity.", "error")
                else:
                    st.session_state.sales_items.append({
                        "item_name": item_name_val.strip(),
                        "quantity": item_qty_val,
                        "selling_price": item_price_val,
                        "gst_rate": item_gst_val,
                        "total_amount": calc_total
                    })
                    notify("Item added successfully.", "success")
                    st.rerun()

        if st.session_state.sales_items:
            st.markdown("##### 🛒 Sales Items")
            calc_rows = []
            overall_sales_total = 0.0

            for idx, itm in enumerate(st.session_state.sales_items):
                base_amt = itm["selling_price"] * itm["quantity"]
                gst_amt = base_amt * (itm["gst_rate"] / 100.0)
                line_total = base_amt + gst_amt
                overall_sales_total += line_total

                calc_rows.append({
                    "#": idx + 1,
                    "Item Name": itm["item_name"],
                    "Quantity": itm["quantity"],
                    "Selling Price": format_currency(itm["selling_price"]),
                    "GST %": f"{itm['gst_rate']}%",
                    "Total Amount": format_currency(line_total)
                })

            st.dataframe(pd.DataFrame(calc_rows), use_container_width=True, hide_index=True)

            if st.button("🗑️ Clear Items", key="clear_sales"):
                st.session_state.sales_items = []
                st.rerun()

            st.markdown("---")
            sb1, sb2 = st.columns([1, 1.2])
            with sb1:
                st.markdown(f"""
                <div class="total-box">
                    <div class="total-box-label">SALES TOTAL</div>
                    <div class="total-box-amount">{format_currency(overall_sales_total)}</div>
                </div>
                """, unsafe_allow_html=True)

            with sb2:
                pay_status = st.selectbox("Payment Status", ["Paid", "Partially Paid", "Unpaid", "Credit"])
                paid_amt = st.number_input("Amount Received from Customer (₹)", min_value=0.0, max_value=float(overall_sales_total), value=float(overall_sales_total) if pay_status == "Paid" else 0.0)
                pay_method = st.selectbox("Payment Mode", ["Cash", "UPI", "Bank Transfer", "Card", "Cheque", "Other"])
                outstanding_amt = overall_sales_total - paid_amt
                st.caption(f"Debitor Receivable Balance: **{format_currency(outstanding_amt)}**")

            if st.button("🚀 Complete Sale & Dispatch Stock", type="primary", use_container_width=True):
                gstin_valid = validate_gstin(party_gstin_in)
                if not party_name_in.strip():
                    notify("Business Name is required.", "error")
                elif not party_phone_in.strip():
                    notify("Mobile Number is required.", "error")
                elif not gstin_valid:
                    notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                else:
                    try:
                        DatabaseAdapter.execute("""
                            INSERT INTO debitor_accounts (tenant_id, name, business_name, phone, gstin, balance) 
                            VALUES (?, ?, ?, ?, ?, 0)
                            ON CONFLICT DO NOTHING
                        """, (t_id, party_name_in.strip(), party_biz_in.strip(), party_phone_in.strip(), party_gstin_in.strip().upper()))
                        DatabaseAdapter.execute("UPDATE debitor_accounts SET balance = balance + ? WHERE tenant_id = ? AND name = ?", (outstanding_amt, t_id, party_name_in.strip()))

                        total_subtotal = sum(i["selling_price"] * i["quantity"] for i in st.session_state.sales_items)
                        total_gst = sum((i["selling_price"] * i["quantity"]) * (i["gst_rate"] / 100.0) for i in st.session_state.sales_items)

                        tx_id = DatabaseAdapter.execute("""
                            INSERT INTO billing_transactions (
                                tenant_id, invoice_number, transaction_type, party_type, party_name, business_name,
                                phone, gstin, invoice_date, due_date, subtotal, gst_total, grand_total,
                                paid_amount, outstanding_amount, payment_status, payment_method, status
                            ) VALUES (?, ?, 'SALE', 'DEBITOR', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')
                        """, (
                            t_id, invoice_num, party_name_in.strip(), party_biz_in.strip(), party_phone_in.strip(), party_gstin_in.strip().upper(),
                            invoice_date_val.strftime("%Y-%m-%d"), due_date_val.strftime("%Y-%m-%d"),
                            total_subtotal, total_gst, overall_sales_total, paid_amt, outstanding_amt, pay_status, pay_method
                        ), fetch="lastrowid")

                        for itm in st.session_state.sales_items:
                            matched_prod_id = get_or_find_product_from_sale(t_id, itm["item_name"])

                            taxable = itm["selling_price"] * itm["quantity"]
                            gst_amt = taxable * (itm["gst_rate"] / 100.0)
                            line_tot = taxable + gst_amt

                            DatabaseAdapter.execute("""
                                INSERT INTO billing_items (
                                    tenant_id, transaction_id, product_id, item_name, item_price, mrp, selling_price,
                                    quantity, gst_rate, gst_amount, total_amount
                                ) VALUES (?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?)
                            """, (t_id, tx_id, matched_prod_id, itm["item_name"], itm["selling_price"], itm["quantity"], itm["gst_rate"], gst_amt, line_tot))

                            if matched_prod_id:
                                adjust_inventory_stock(matched_prod_id, -itm["quantity"], "Sales Dispatch", invoice_num, f"Sold to {party_name_in}")

                        update_party_ledger("DEBITOR", party_name_in.strip(), f"Sales Invoice {invoice_num}", debit=overall_sales_total, credit=paid_amt, reference=invoice_num)
                        log_tenant_audit("SALE_CREATED", f"Recorded sales invoice {invoice_num} for {party_name_in}")
                        st.session_state.sales_items = []
                        notify("Sale completed successfully.", "success")
                        st.rerun()
                    except Exception as err:
                        notify(format_error(err), "error")
        else:
            st.info("Add items to your sales invoice above.")

    with sales_tab2:
        st.markdown("#### 📜 Sales History & Tax Invoices")
        sales_df = DatabaseAdapter.read_df("SELECT * FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' ORDER BY id DESC", [t_id])
        st.dataframe(sales_df[["id", "invoice_number", "invoice_date", "party_name", "grand_total", "paid_amount", "outstanding_amount", "payment_status", "status"]] if not sales_df.empty else sales_df, use_container_width=True, hide_index=True)

        if not sales_df.empty:
            sel_pid_s = st.selectbox("Select Tax Invoice", sales_df["id"].tolist(), format_func=lambda x: f"{sales_df[sales_df['id'] == x]['invoice_number'].values[0]} — {sales_df[sales_df['id'] == x]['party_name'].values[0]}")
            tx_row = DatabaseAdapter.execute("SELECT * FROM billing_transactions WHERE id = ? AND tenant_id = ?", (sel_pid_s, t_id), fetch="one")
            items_row = DatabaseAdapter.execute("SELECT * FROM billing_items WHERE transaction_id = ? AND tenant_id = ?", (sel_pid_s, t_id), fetch="all") or []
            if tx_row:
                pdf_bytes = generate_invoice_pdf(dict(tx_row), [dict(r) for r in items_row], settings)
                st.download_button("📥 Download Tax Invoice PDF", data=pdf_bytes, file_name=f"{tx_row['invoice_number']}.pdf", mime="application/pdf", type="primary")

                st.markdown("---")
                with st.expander("🗑️ Delete Sale Record"):
                    st.warning("⚠️ Deleting this sale will reverse the inventory stock reduction and recalculate debitor balances.")
                    conf_del_s = st.checkbox("Confirm sale deletion", key=f"conf_del_s_{sel_pid_s}")
                    if st.button("Delete Sale Permanently", type="primary", disabled=not conf_del_s):
                        try:
                            for itm in items_row:
                                if itm["product_id"]:
                                    adjust_inventory_stock(itm["product_id"], itm["quantity"], "Sale Deletion", tx_row["invoice_number"], "Reversed due to sale deletion")
                            DatabaseAdapter.execute("UPDATE debitor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (tx_row["outstanding_amount"], t_id, tx_row["party_name"]))
                            DatabaseAdapter.execute("DELETE FROM billing_transactions WHERE id = ? AND tenant_id = ?", (sel_pid_s, t_id))
                            log_tenant_audit("SALE_DELETE", f"Deleted sales invoice {tx_row['invoice_number']}")
                            notify("Sale deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")

    with sales_tab3:
        st.markdown("#### 🗑️ Clear All Sales")
        st.warning("⚠️ **WARNING:** You are about to permanently clear all sales transactions for this business.")
        clear_conf_sales = st.text_input("Type 'CLEAR ALL' to confirm sales clearance", key="clear_sales_text")
        if st.button("Confirm Clear All Sales", type="primary"):
            if clear_conf_sales.strip() == "CLEAR ALL":
                try:
                    s_txs = DatabaseAdapter.execute("SELECT id FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE'", (t_id,), fetch="all") or []
                    for stx in s_txs:
                        items_row = DatabaseAdapter.execute("SELECT * FROM billing_items WHERE transaction_id = ? AND tenant_id = ?", (stx["id"], t_id), fetch="all") or []
                        tx_row = DatabaseAdapter.execute("SELECT * FROM billing_transactions WHERE id = ? AND tenant_id = ?", (stx["id"], t_id), fetch="one")
                        for itm in items_row:
                            if itm["product_id"]:
                                adjust_inventory_stock(itm["product_id"], itm["quantity"], "Sale Deletion", tx_row["invoice_number"], "Reversed due to bulk clearance")
                        if tx_row:
                            DatabaseAdapter.execute("UPDATE debitor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (tx_row["outstanding_amount"], t_id, tx_row["party_name"]))

                    DatabaseAdapter.execute("DELETE FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE'", (t_id,))
                    log_tenant_audit("CLEAR_ALL_SALES", "Cleared all sales records for tenant.")
                    notify("All sales entries have been cleared successfully.", "success")
                    st.rerun()
                except Exception as e:
                    notify(format_error(e), "error")
            else:
                notify("Confirmation text did not match 'CLEAR ALL'.", "error")


# =====================================================================
# MODULE 5: PAYMENTS MODULE
# =====================================================================
elif menu == "💳 Payments":
    st.title("Payments & Settlements Hub")
    st.caption("Manage creditor payables and debitor receivables with Cash/Bank routing and transaction logging.")

    t_id = st.session_state.tenant_id

    pay_tab_sel = st.radio("PAYMENT CATEGORY", ["💳 CREDITOR (Amounts We Owe)", "💰 DEBITOR (Amounts Owed To Us)"], horizontal=True)
    is_creditor_pay = "CREDITOR" in pay_tab_sel

    st.markdown("---")

    if is_creditor_pay:
        st.markdown("### 💳 Creditor Payables & Settlement")

        cred_purchases = DatabaseAdapter.read_df("SELECT party_name, SUM(grand_total) as total_pur FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND status = 'Active' GROUP BY party_name", [t_id])
        cred_payments = DatabaseAdapter.read_df("SELECT party_name, payment_method, SUM(amount) as total_paid FROM payments_ledger WHERE tenant_id = ? AND party_type = 'CREDITOR' GROUP BY party_name, payment_method", [t_id])

        creditors = DatabaseAdapter.execute("SELECT * FROM creditor_accounts WHERE tenant_id = ?", (t_id,), fetch="all") or []

        total_payable = cred_purchases["total_pur"].sum() if not cred_purchases.empty else 0.0
        total_paid = cred_payments["total_paid"].sum() if not cred_payments.empty else 0.0
        total_pending = max(0.0, total_payable - total_paid)

        cash_paid = cred_payments[cred_payments["payment_method"] == "Cash"]["total_paid"].sum() if not cred_payments.empty else 0.0
        bank_paid = cred_payments[cred_payments["payment_method"] == "Bank"]["total_paid"].sum() if not cred_payments.empty else 0.0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Payable", format_currency(total_payable))
        m2.metric("Total Paid", format_currency(total_paid))
        m3.metric("Total Pending", format_currency(total_pending))
        m4.metric("Cash Paid", format_currency(cash_paid))
        m5.metric("Bank Paid", format_currency(bank_paid))

        st.markdown("---")
        st.markdown("#### 📋 Creditor Account Balances")

        cred_rows = []
        for c in creditors:
            c_name = c["name"]
            c_pur = cred_purchases[cred_purchases["party_name"] == c_name]["total_pur"].sum() if not cred_purchases.empty else 0.0
            c_pd = cred_payments[cred_payments["party_name"] == c_name]["total_paid"].sum() if not cred_payments.empty else 0.0
            c_pen = max(0.0, c_pur - c_pd)

            if c_pen == 0 and c_pur > 0:
                status_badge = "🟢 PAID"
            elif c_pd > 0 and c_pen > 0:
                status_badge = "🟡 PARTIALLY PAID"
            else:
                status_badge = "🔴 PENDING"

            cred_rows.append({
                "Business Name": c_name,
                "Total Purchase": format_currency(c_pur),
                "Paid": format_currency(c_pd),
                "Pending": format_currency(c_pen),
                "Status": status_badge,
                "raw_pending": c_pen
            })

        if cred_rows:
            cred_df = pd.DataFrame(cred_rows)
            st.dataframe(cred_df[["Business Name", "Total Purchase", "Paid", "Pending", "Status"]], use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("##### 💸 Record Creditor Payment")
            with st.form("creditor_payment_form"):
                cp1, cp2 = st.columns(2)
                target_cred = cp1.selectbox("Select Business Name", [r["Business Name"] for r in cred_rows if r["raw_pending"] > 0] if any(r["raw_pending"] > 0 for r in cred_rows) else [])

                matched_row = next((r for r in cred_rows if r["Business Name"] == target_cred), None)
                max_pend = matched_row["raw_pending"] if matched_row else 0.0

                cp_amount = cp2.number_input(f"Payment Amount (Max Pending: ₹{max_pend:,.2f})", min_value=0.0, max_value=float(max_pend), value=float(max_pend), step=100.0)

                cp3, cp4 = st.columns(2)
                cp_method = cp3.selectbox("Payment Method", ["Cash", "Bank"])
                cp_date = cp4.date_input("Payment Date", value=date.today())

                cp_ref = st.text_input("Bank/Transaction Reference Number (Required if Bank selected)", placeholder="e.g. UPI/NEFT/Cheque ID")
                cp_notes = st.text_input("Notes / Remarks", placeholder="e.g. Settlement for purchase invoice")

                if st.form_submit_button("Record Creditor Payment", type="primary"):
                    if not target_cred:
                        notify("Please select a creditor business.", "error")
                    elif cp_amount <= 0:
                        notify("Payment amount must be greater than zero.", "error")
                    elif cp_amount > max_pend:
                        notify("Payment cannot exceed the pending amount.", "error")
                    elif cp_method == "Bank" and not cp_ref.strip():
                        notify("Please enter the bank/transaction reference number.", "error")
                    else:
                        try:
                            DatabaseAdapter.execute("""
                                INSERT INTO payments_ledger (tenant_id, payment_date, party_type, party_name, payment_method, reference_number, amount, notes)
                                VALUES (?, ?, 'CREDITOR', ?, ?, ?, ?, ?)
                            """, (t_id, cp_date.strftime("%Y-%m-%d"), target_cred, cp_method, cp_ref.strip() if cp_method == "Bank" else "—", cp_amount, cp_notes.strip()))

                            DatabaseAdapter.execute("UPDATE creditor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (cp_amount, t_id, target_cred))
                            update_party_ledger("CREDITOR", target_cred, f"Payment Made ({cp_method}) Ref: {cp_ref or '—'}", debit=cp_amount, credit=0, reference=cp_ref)

                            log_tenant_audit("CREDITOR_PAYMENT", f"Paid {cp_amount} to {target_cred} via {cp_method}")
                            notify("Creditor payment recorded successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")
        else:
            st.info("No active creditor payables found.")

    else:
        st.markdown("### 💰 Debitor Receivables & Settlement")

        deb_sales = DatabaseAdapter.read_df("SELECT party_name, SUM(grand_total) as total_sal FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND status = 'Active' GROUP BY party_name", [t_id])
        deb_payments = DatabaseAdapter.read_df("SELECT party_name, payment_method, SUM(amount) as total_rec FROM payments_ledger WHERE tenant_id = ? AND party_type = 'DEBITOR' GROUP BY party_name, payment_method", [t_id])

        debtors = DatabaseAdapter.execute("SELECT * FROM debitor_accounts WHERE tenant_id = ?", (t_id,), fetch="all") or []

        total_receivable = deb_sales["total_sal"].sum() if not deb_sales.empty else 0.0
        total_received = deb_payments["total_rec"].sum() if not deb_payments.empty else 0.0
        total_pending_deb = max(0.0, total_receivable - total_received)

        cash_rec = deb_payments[deb_payments["payment_method"] == "Cash"]["total_rec"].sum() if not deb_payments.empty else 0.0
        bank_rec = deb_payments[deb_payments["payment_method"] == "Bank"]["total_rec"].sum() if not deb_payments.empty else 0.0

        dm1, dm2, dm3, dm4, dm5 = st.columns(5)
        dm1.metric("Total Receivable", format_currency(total_receivable))
        dm2.metric("Total Received", format_currency(total_received))
        dm3.metric("Total Pending", format_currency(total_pending_deb))
        dm4.metric("Cash Received", format_currency(cash_rec))
        dm5.metric("Bank Received", format_currency(bank_rec))

        st.markdown("---")
        st.markdown("#### 📋 Debitor Account Balances")

        deb_rows = []
        for d in debtors:
            d_name = d["name"]
            d_sal = deb_sales[deb_sales["party_name"] == d_name]["total_sal"].sum() if not deb_sales.empty else 0.0
            d_rc = deb_payments[deb_payments["party_name"] == d_name]["total_rec"].sum() if not deb_payments.empty else 0.0
            d_pen = max(0.0, d_sal - d_rc)

            if d_pen == 0 and d_sal > 0:
                status_badge = "🟢 PAID"
            elif d_rc > 0 and d_pen > 0:
                status_badge = "🟡 PARTIALLY PAID"
            else:
                status_badge = "🔴 PENDING"

            deb_rows.append({
                "Business Name": d_name,
                "Total Sales": format_currency(d_sal),
                "Received": format_currency(d_rc),
                "Pending": format_currency(d_pen),
                "Status": status_badge,
                "raw_pending": d_pen
            })

        if deb_rows:
            deb_df = pd.DataFrame(deb_rows)
            st.dataframe(deb_df[["Business Name", "Total Sales", "Received", "Pending", "Status"]], use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("##### 📥 Receive Debitor Payment")
            with st.form("debitor_payment_form"):
                dp1, dp2 = st.columns(2)
                target_deb = dp1.selectbox("Select Business Name", [r["Business Name"] for r in deb_rows if r["raw_pending"] > 0] if any(r["raw_pending"] > 0 for r in deb_rows) else [])

                matched_deb_row = next((r for r in deb_rows if r["Business Name"] == target_deb), None)
                max_deb_pend = matched_deb_row["raw_pending"] if matched_deb_row else 0.0

                dp_amount = dp2.number_input(f"Received Amount (Max Pending: ₹{max_deb_pend:,.2f})", min_value=0.0, max_value=float(max_deb_pend), value=float(max_deb_pend), step=100.0)

                dp3, dp4 = st.columns(2)
                dp_method = dp3.selectbox("Payment Method", ["Cash", "Bank"])
                dp_date = dp4.date_input("Payment Date", value=date.today())

                dp_ref = st.text_input("Bank/Transaction Reference Number (Required if Bank selected)", placeholder="e.g. UPI/NEFT/Cheque ID")
                dp_notes = st.text_input("Notes / Remarks", placeholder="e.g. Payment received against sales invoice")

                if st.form_submit_button("Receive Payment", type="primary"):
                    if not target_deb:
                        notify("Please select a customer business.", "error")
                    elif dp_amount <= 0:
                        notify("Received amount must be greater than zero.", "error")
                    elif dp_amount > max_deb_pend:
                        notify("Payment cannot exceed the pending amount.", "error")
                    elif dp_method == "Bank" and not dp_ref.strip():
                        notify("Please enter the bank/transaction reference number.", "error")
                    else:
                        try:
                            DatabaseAdapter.execute("""
                                INSERT INTO payments_ledger (tenant_id, payment_date, party_type, party_name, payment_method, reference_number, amount, notes)
                                VALUES (?, ?, 'DEBITOR', ?, ?, ?, ?, ?)
                            """, (t_id, dp_date.strftime("%Y-%m-%d"), target_deb, dp_method, dp_ref.strip() if dp_method == "Bank" else "—", dp_amount, dp_notes.strip()))

                            DatabaseAdapter.execute("UPDATE debitor_accounts SET balance = balance - ? WHERE tenant_id = ? AND name = ?", (dp_amount, t_id, target_deb))
                            update_party_ledger("DEBITOR", target_deb, f"Payment Received ({dp_method}) Ref: {dp_ref or '—'}", debit=0, credit=dp_amount, reference=dp_ref)

                            log_tenant_audit("DEBITOR_PAYMENT", f"Received {dp_amount} from {target_deb} via {dp_method}")
                            notify("Debitor payment received successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")
        else:
            st.info("No active debitor receivables found.")

    st.markdown("---")
    st.markdown("#### 📜 Payment Audit History & Deletion")
    payment_hist_df = DatabaseAdapter.read_df("""
        SELECT id, payment_date, party_name, party_type, payment_method, reference_number, amount, notes
        FROM payments_ledger WHERE tenant_id = ? ORDER BY id DESC
    """, [t_id])

    if not payment_hist_df.empty:
        disp_pay_df = payment_hist_df.copy()
        disp_pay_df.columns = ["ID", "Date", "Business Name", "Type", "Method", "Reference", "Amount", "Notes"]
        disp_pay_df["Amount"] = disp_pay_df["Amount"].apply(format_currency)
        st.dataframe(disp_pay_df[["Date", "Business Name", "Type", "Method", "Reference", "Amount", "Notes"]], use_container_width=True, hide_index=True)

        st.markdown("---")
        with st.expander("🗑️ Delete Individual Payment Record"):
            sel_pay_id = st.selectbox("Select Payment Record to Delete", payment_hist_df["id"].tolist(), format_func=lambda x: f"Payment ID {x} — {payment_hist_df[payment_hist_df['id'] == x]['party_name'].values[0]} ({format_currency(payment_hist_df[payment_hist_df['id'] == x]['amount'].values[0])})")
            conf_del_pay = st.checkbox("Confirm payment deletion", key=f"conf_del_pay_{sel_pay_id}")
            if st.button("Delete Payment Record", type="primary", disabled=not conf_del_pay):
                try:
                    p_row = DatabaseAdapter.execute("SELECT * FROM payments_ledger WHERE id = ? AND tenant_id = ?", (sel_pay_id, t_id), fetch="one")
                    if p_row:
                        if p_row["party_type"] == "CREDITOR":
                            DatabaseAdapter.execute("UPDATE creditor_accounts SET balance = balance + ? WHERE tenant_id = ? AND name = ?", (p_row["amount"], t_id, p_row["party_name"]))
                        else:
                            DatabaseAdapter.execute("UPDATE debitor_accounts SET balance = balance + ? WHERE tenant_id = ? AND name = ?", (p_row["amount"], t_id, p_row["party_name"]))

                        DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE id = ? AND tenant_id = ?", (sel_pay_id, t_id))
                        log_tenant_audit("PAYMENT_DELETE", f"Deleted payment ID {sel_pay_id}")
                        notify("Payment deleted successfully.", "success")
                        st.rerun()
                except Exception as e:
                    notify(format_error(e), "error")
    else:
        st.info("No payment transactions recorded yet.")

    st.markdown("---")
    with st.expander("🗑️ Clear All Payments"):
        st.warning("⚠️ **WARNING:** You are about to permanently clear all payment records for this business.")
        clear_conf_pay = st.text_input("Type 'CLEAR ALL' to confirm payments clearance", key="clear_pay_text")
        if st.button("Confirm Clear All Payments", type="primary"):
            if clear_conf_pay.strip() == "CLEAR ALL":
                try:
                    DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE tenant_id = ?", (t_id,))
                    log_tenant_audit("CLEAR_ALL_PAYMENTS", "Cleared all payments ledger for tenant.")
                    notify("All payment entries have been cleared successfully.", "success")
                    st.rerun()
                except Exception as e:
                    notify(format_error(e), "error")
            else:
                notify("Confirmation text did not match 'CLEAR ALL'.", "error")


# =====================================================================
# MODULE 5.1: CREDITORS & DEBTORS SECTION
# =====================================================================
elif menu == "👥 Creditors & Debtors":
    st.title("Creditors & Debtors Management")
    st.caption("Central overview of all accounts payable to suppliers and accounts receivable from customers.")
    st.markdown("---")

    t_id = st.session_state.tenant_id

    cd_tab_cred, cd_tab_deb = st.tabs(["💳 Creditors", "💰 Debtors"])

    # -----------------------------------------------------------------
    # CREDITOR TAB
    # -----------------------------------------------------------------
    with cd_tab_cred:
        st.markdown("### Creditors Summary")
        cred_purchases = DatabaseAdapter.read_df("SELECT party_name, phone, SUM(grand_total) as total_pur FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND status = 'Active' GROUP BY party_name, phone", [t_id])
        cred_payments = DatabaseAdapter.read_df("SELECT party_name, SUM(amount) as total_paid FROM payments_ledger WHERE tenant_id = ? AND party_type = 'CREDITOR' GROUP BY party_name", [t_id])
        creditors = DatabaseAdapter.execute("SELECT * FROM creditor_accounts WHERE tenant_id = ?", (t_id,), fetch="all") or []

        total_cred_count = len(creditors)
        total_pur_amt = cred_purchases["total_pur"].sum() if not cred_purchases.empty else 0.0
        total_paid_amt = cred_payments["total_paid"].sum() if not cred_payments.empty else 0.0
        total_payable_amt = max(0.0, total_pur_amt - total_paid_amt)

        cred_master_rows = []
        for c in creditors:
            c_name = c["name"]
            c_phone = c["phone"] or "N/A"
            c_pur = cred_purchases[cred_purchases["party_name"] == c_name]["total_pur"].sum() if not cred_purchases.empty else 0.0
            c_pd = cred_payments[cred_payments["party_name"] == c_name]["total_paid"].sum() if not cred_payments.empty else 0.0
            c_pen = max(0.0, c_pur - c_pd)

            last_tx = DatabaseAdapter.execute("SELECT invoice_date FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND party_name = ? ORDER BY id DESC LIMIT 1", (t_id, c_name), fetch="one")
            last_dt = last_tx["invoice_date"] if last_tx else "—"

            if c_pen == 0:
                st_text = "Paid"
            elif c_pd > 0 and c_pen > 0:
                st_text = "Partially Paid"
            else:
                st_text = "Pending"

            cred_master_rows.append({
                "id": c["id"],
                "Creditor Name": c_name,
                "Mobile Number": c_phone,
                "Total Purchase": c_pur,
                "Amount Paid": c_pd,
                "Amount Payable": c_pen,
                "Last Transaction": last_dt,
                "Payment Status": st_text
            })

        pending_cred_count = sum(1 for r in cred_master_rows if r["Amount Payable"] > 0)

        cs1, cs2, cs3, cs4, cs5 = st.columns(5)
        cs1.metric("Total Creditors", total_cred_count)
        cs2.metric("Total Purchase Amount", format_currency(total_pur_amt))
        cs3.metric("Total Amount Paid", format_currency(total_paid_amt))
        cs4.metric("Total Amount Payable", format_currency(total_payable_amt))
        cs5.metric("Pending Creditors", pending_cred_count)

        st.markdown("---")
        st.markdown("#### Creditor Directory & Search")

        sc1, sc2, sc3 = st.columns([2, 1.5, 1.5])
        c_search = sc1.text_input("🔍 Search by Name or Mobile", key="cred_search_input")
        c_filter = sc2.selectbox("Filter Status", ["All", "Paid", "Partially Paid", "Pending"], key="cred_filter_sel")
        c_sort = sc3.selectbox("Sort By", ["Highest payable first", "Lowest payable first", "Name A–Z", "Name Z–A", "Latest transaction", "Oldest transaction"], key="cred_sort_sel")

        with st.expander("🗑️ Clear All Creditors"):
            st.warning("WARNING: This will permanently delete all creditor records and their associated creditor ledger/payment records. This action cannot be undone.")
            clear_c_text = st.text_input("Type explicit confirmation to clear all creditors", placeholder="Type 'CLEAR ALL' here", key="clear_cred_text_input")
            if st.button("Confirm Clear All Creditors", type="primary"):
                if clear_c_text.strip() == "CLEAR ALL":
                    try:
                        DatabaseAdapter.execute("DELETE FROM creditor_accounts WHERE tenant_id = ?", (t_id,))
                        DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE tenant_id = ? AND party_type = 'CREDITOR'", (t_id,))
                        log_tenant_audit("CLEAR_ALL_CREDITORS", "Cleared all creditor accounts and payments.")
                        notify("All creditor records cleared successfully.", "success")
                        st.rerun()
                    except Exception as e:
                        notify(format_error(e), "error")
                else:
                    notify("Confirmation text did not match.", "error")

        filtered_creds = cred_master_rows.copy()
        if c_search.strip():
            kw = c_search.strip().lower()
            filtered_creds = [r for r in filtered_creds if kw in r["Creditor Name"].lower() or kw in r["Mobile Number"].lower()]
        if c_filter != "All":
            filtered_creds = [r for r in filtered_creds if r["Payment Status"].lower() == c_filter.lower()]

        if c_sort == "Highest payable first":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Amount Payable"], reverse=True)
        elif c_sort == "Lowest payable first":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Amount Payable"])
        elif c_sort == "Name A–Z":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Creditor Name"])
        elif c_sort == "Name Z–A":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Creditor Name"], reverse=True)
        elif c_sort == "Latest transaction":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Last Transaction"], reverse=True)
        elif c_sort == "Oldest transaction":
            filtered_creds = sorted(filtered_creds, key=lambda x: x["Last Transaction"])

        if filtered_creds:
            disp_cred_df = pd.DataFrame(filtered_creds)
            disp_cred_df_show = disp_cred_df.copy()
            disp_cred_df_show["Total Purchase"] = disp_cred_df_show["Total Purchase"].apply(format_currency)
            disp_cred_df_show["Amount Paid"] = disp_cred_df_show["Amount Paid"].apply(format_currency)
            disp_cred_df_show["Amount Payable"] = disp_cred_df_show["Amount Payable"].apply(format_currency)

            st.dataframe(disp_cred_df_show[["Creditor Name", "Mobile Number", "Total Purchase", "Amount Paid", "Amount Payable", "Last Transaction", "Payment Status"]], use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("#### Creditor Detailed Ledger & Individual Actions")
            sel_cred_name = st.selectbox("Select Creditor to View Ledger / Delete", [r["Creditor Name"] for r in filtered_creds], key="sel_cred_ledger_box")

            chosen_cred_row = next((r for r in filtered_creds if r["Creditor Name"] == sel_cred_name), None)
            if chosen_cred_row:
                c_col1, c_col2 = st.columns(2)
                c_col1.markdown(f"**Creditor Name:** {chosen_cred_row['Creditor Name']}")
                c_col1.markdown(f"**Mobile Number:** {chosen_cred_row['Mobile Number']}")
                c_col2.markdown(f"**Total Payable:** {format_currency(chosen_cred_row['Amount Payable'])}")
                c_col2.markdown(f"**Status:** {chosen_cred_row['Payment Status']}")

                st.markdown("##### Related Purchase Transactions")
                c_purch_history = DatabaseAdapter.read_df("SELECT invoice_date as Date, invoice_number as 'Invoice Number', grand_total as 'Purchase Amount', paid_amount as 'Amount Paid', outstanding_amount as 'Amount Pending', payment_method as 'Payment Method', 'Purchase' as 'Transaction Type' FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND party_name = ? ORDER BY id DESC", [t_id, sel_cred_name])
                if not c_purch_history.empty:
                    c_purch_history["Purchase Amount"] = c_purch_history["Purchase Amount"].apply(format_currency)
                    c_purch_history["Amount Paid"] = c_purch_history["Amount Paid"].apply(format_currency)
                    c_purch_history["Amount Pending"] = c_purch_history["Amount Pending"].apply(format_currency)
                    st.dataframe(c_purch_history, use_container_width=True, hide_index=True)
                else:
                    st.info("No purchase transactions found for this creditor.")

                st.markdown("##### Payment History")
                c_pay_history = DatabaseAdapter.read_df("SELECT payment_date as Date, '—' as 'Invoice Number', 0.0 as 'Purchase Amount', amount as 'Amount Paid', 0.0 as 'Amount Pending', payment_method as 'Payment Method', 'Creditor Payment' as 'Transaction Type' FROM payments_ledger WHERE tenant_id = ? AND party_type = 'CREDITOR' AND party_name = ? ORDER BY id DESC", [t_id, sel_cred_name])
                if not c_pay_history.empty:
                    c_pay_history["Purchase Amount"] = c_pay_history["Purchase Amount"].apply(format_currency)
                    c_pay_history["Amount Paid"] = c_pay_history["Amount Paid"].apply(format_currency)
                    c_pay_history["Amount Pending"] = c_pay_history["Amount Pending"].apply(format_currency)
                    st.dataframe(c_pay_history, use_container_width=True, hide_index=True)
                else:
                    st.info("No payment history found for this creditor.")

                st.markdown("---")
                with st.expander(f"🗑️ Delete Creditor: {sel_cred_name}"):
                    st.warning("Are you sure you want to delete this creditor? Deleting this creditor may remove or affect its associated ledger/payment records.")
                    conf_del_cred_ind = st.checkbox("Confirm deletion of this creditor", key=f"conf_del_cred_{chosen_cred_row['id']}")
                    if st.button("Delete Creditor", type="primary", disabled=not conf_del_cred_ind):
                        try:
                            DatabaseAdapter.execute("DELETE FROM creditor_accounts WHERE id = ? AND tenant_id = ?", (chosen_cred_row["id"], t_id))
                            DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE tenant_id = ? AND party_type = 'CREDITOR' AND party_name = ?", (t_id, sel_cred_name))
                            log_tenant_audit("CREDITOR_DELETE", f"Deleted creditor account: {sel_cred_name}")
                            notify("Creditor deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")
        else:
            st.info("No creditors match your search criteria.")

    # -----------------------------------------------------------------
    # DEBTOR TAB
    # -----------------------------------------------------------------
    with cd_tab_deb:
        st.markdown("### Debtors Summary")
        deb_sales = DatabaseAdapter.read_df("SELECT party_name, phone, SUM(grand_total) as total_sal FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND status = 'Active' GROUP BY party_name, phone", [t_id])
        deb_payments = DatabaseAdapter.read_df("SELECT party_name, SUM(amount) as total_rec FROM payments_ledger WHERE tenant_id = ? AND party_type = 'DEBITOR' GROUP BY party_name", [t_id])
        debtors = DatabaseAdapter.execute("SELECT * FROM debitor_accounts WHERE tenant_id = ?", (t_id,), fetch="all") or []

        total_deb_count = len(debtors)
        total_sal_amt = deb_sales["total_sal"].sum() if not deb_sales.empty else 0.0
        total_rec_amt = deb_payments["total_rec"].sum() if not deb_payments.empty else 0.0
        total_receivable_amt = max(0.0, total_sal_amt - total_rec_amt)

        deb_master_rows = []
        for d in debtors:
            d_name = d["name"]
            d_phone = d["phone"] or "N/A"
            d_sal = deb_sales[deb_sales["party_name"] == d_name]["total_sal"].sum() if not deb_sales.empty else 0.0
            d_rc = deb_payments[deb_payments["party_name"] == d_name]["total_rec"].sum() if not deb_payments.empty else 0.0
            d_pen = max(0.0, d_sal - d_rc)

            last_tx_d = DatabaseAdapter.execute("SELECT invoice_date FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND party_name = ? ORDER BY id DESC LIMIT 1", (t_id, d_name), fetch="one")
            last_dt_d = last_tx_d["invoice_date"] if last_tx_d else "—"

            if d_pen == 0:
                st_text_d = "Received/Paid"
            elif d_rc > 0 and d_pen > 0:
                st_text_d = "Partially Received"
            else:
                st_text_d = "Pending"

            deb_master_rows.append({
                "id": d["id"],
                "Debtor Name": d_name,
                "Mobile Number": d_phone,
                "Total Sales": d_sal,
                "Amount Received": d_rc,
                "Amount Receivable": d_pen,
                "Last Transaction": last_dt_d,
                "Payment Status": st_text_d
            })

        pending_deb_count = sum(1 for r in deb_master_rows if r["Amount Receivable"] > 0)

        ds1, ds2, ds3, ds4, ds5 = st.columns(5)
        ds1.metric("Total Debtors", total_deb_count)
        ds2.metric("Total Sales Amount", format_currency(total_sal_amt))
        ds3.metric("Total Amount Received", format_currency(total_rec_amt))
        ds4.metric("Total Amount Receivable", format_currency(total_receivable_amt))
        ds5.metric("Pending Debtors", pending_deb_count)

        st.markdown("---")
        st.markdown("#### Debtor Directory & Search")

        dc1, dc2, dc3 = st.columns([2, 1.5, 1.5])
        d_search = dc1.text_input("🔍 Search by Name or Mobile", key="deb_search_input")
        d_filter = dc2.selectbox("Filter Status", ["All", "Received/Paid", "Partially Received", "Pending"], key="deb_filter_sel")
        d_sort = dc3.selectbox("Sort By", ["Highest receivable first", "Lowest receivable first", "Name A–Z", "Name Z–A", "Latest transaction", "Oldest transaction"], key="deb_sort_sel")

        with st.expander("🗑️ Clear All Debtors"):
            st.warning("WARNING: This will permanently delete all debtor records and their associated debtor ledger/payment records. This action cannot be undone.")
            clear_d_text = st.text_input("Type explicit confirmation to clear all debtors", placeholder="Type 'CLEAR ALL' here", key="clear_deb_text_input")
            if st.button("Confirm Clear All Debtors", type="primary"):
                if clear_d_text.strip() == "CLEAR ALL":
                    try:
                        DatabaseAdapter.execute("DELETE FROM debitor_accounts WHERE tenant_id = ?", (t_id,))
                        DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE tenant_id = ? AND party_type = 'DEBITOR'", (t_id,))
                        log_tenant_audit("CLEAR_ALL_DEBTORS", "Cleared all debtor accounts and payments.")
                        notify("All debtor records cleared successfully.", "success")
                        st.rerun()
                    except Exception as e:
                        notify(format_error(e), "error")
                else:
                    notify("Confirmation text did not match.", "error")

        filtered_debs = deb_master_rows.copy()
        if d_search.strip():
            kw = d_search.strip().lower()
            filtered_debs = [r for r in filtered_debs if kw in r["Debtor Name"].lower() or kw in r["Mobile Number"].lower()]
        if d_filter != "All":
            filtered_debs = [r for r in filtered_debs if r["Payment Status"].lower() == d_filter.lower()]

        if d_sort == "Highest receivable first":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Amount Receivable"], reverse=True)
        elif d_sort == "Lowest receivable first":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Amount Receivable"])
        elif d_sort == "Name A–Z":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Debtor Name"])
        elif d_sort == "Name Z–A":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Debtor Name"], reverse=True)
        elif d_sort == "Latest transaction":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Last Transaction"], reverse=True)
        elif d_sort == "Oldest transaction":
            filtered_debs = sorted(filtered_debs, key=lambda x: x["Last Transaction"])

        if filtered_debs:
            disp_deb_df = pd.DataFrame(filtered_debs)
            disp_deb_df_show = disp_deb_df.copy()
            disp_deb_df_show["Total Sales"] = disp_deb_df_show["Total Sales"].apply(format_currency)
            disp_deb_df_show["Amount Received"] = disp_deb_df_show["Amount Received"].apply(format_currency)
            disp_deb_df_show["Amount Receivable"] = disp_deb_df_show["Amount Receivable"].apply(format_currency)

            st.dataframe(disp_deb_df_show[["Debtor Name", "Mobile Number", "Total Sales", "Amount Received", "Amount Receivable", "Last Transaction", "Payment Status"]], use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("#### Debtor Detailed Ledger & Individual Actions")
            sel_deb_name = st.selectbox("Select Debtor to View Ledger / Delete", [r["Debtor Name"] for r in filtered_debs], key="sel_deb_ledger_box")

            chosen_deb_row = next((r for r in filtered_debs if r["Debtor Name"] == sel_deb_name), None)
            if chosen_deb_row:
                d_col1, d_col2 = st.columns(2)
                d_col1.markdown(f"**Debtor Name:** {chosen_deb_row['Debtor Name']}")
                d_col1.markdown(f"**Mobile Number:** {chosen_deb_row['Mobile Number']}")
                d_col2.markdown(f"**Total Receivable:** {format_currency(chosen_deb_row['Amount Receivable'])}")
                d_col2.markdown(f"**Status:** {chosen_deb_row['Payment Status']}")

                st.markdown("##### Related Sales Transactions")
                d_sales_history = DatabaseAdapter.read_df("SELECT invoice_date as Date, invoice_number as 'Invoice Number', grand_total as 'Sales Amount', paid_amount as 'Amount Received', outstanding_amount as 'Amount Pending', payment_method as 'Payment Method', 'Sale' as 'Transaction Type' FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND party_name = ? ORDER BY id DESC", [t_id, sel_deb_name])
                if not d_sales_history.empty:
                    d_sales_history["Sales Amount"] = d_sales_history["Sales Amount"].apply(format_currency)
                    d_sales_history["Amount Received"] = d_sales_history["Amount Received"].apply(format_currency)
                    d_sales_history["Amount Pending"] = d_sales_history["Amount Pending"].apply(format_currency)
                    st.dataframe(d_sales_history, use_container_width=True, hide_index=True)
                else:
                    st.info("No sales transactions found for this debtor.")

                st.markdown("##### Payment History")
                d_pay_history = DatabaseAdapter.read_df("SELECT payment_date as Date, '—' as 'Invoice Number', 0.0 as 'Sales Amount', amount as 'Amount Received', 0.0 as 'Amount Pending', payment_method as 'Payment Method', 'Debtor Payment' as 'Transaction Type' FROM payments_ledger WHERE tenant_id = ? AND party_type = 'DEBITOR' AND party_name = ? ORDER BY id DESC", [t_id, sel_deb_name])
                if not d_pay_history.empty:
                    d_pay_history["Sales Amount"] = d_pay_history["Sales Amount"].apply(format_currency)
                    d_pay_history["Amount Received"] = d_pay_history["Amount Received"].apply(format_currency)
                    d_pay_history["Amount Pending"] = d_pay_history["Amount Pending"].apply(format_currency)
                    st.dataframe(d_pay_history, use_container_width=True, hide_index=True)
                else:
                    st.info("No payment history found for this debtor.")

                st.markdown("---")
                with st.expander(f"🗑️ Delete Debtor: {sel_deb_name}"):
                    st.warning("Are you sure you want to delete this debtor? Deleting this debtor may remove or affect its associated sales/payment/ledger records.")
                    conf_del_deb_ind = st.checkbox("Confirm deletion of this debtor", key=f"conf_del_deb_{chosen_deb_row['id']}")
                    if st.button("Delete Debtor", type="primary", disabled=not conf_del_deb_ind):
                        try:
                            DatabaseAdapter.execute("DELETE FROM debitor_accounts WHERE id = ? AND tenant_id = ?", (chosen_deb_row["id"], t_id))
                            DatabaseAdapter.execute("DELETE FROM payments_ledger WHERE tenant_id = ? AND party_type = 'DEBITOR' AND party_name = ?", (t_id, sel_deb_name))
                            log_tenant_audit("DEBITOR_DELETE", f"Deleted debtor account: {sel_deb_name}")
                            notify("Debtor deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")
        else:
            st.info("No debtors match your search criteria.")


# =====================================================================
# MODULE 6: INVENTORY OPS
# =====================================================================
elif menu == "📊 Inventory Ops":
    st.title("Inventory Operations")
    st.caption("Quick stock inward, outward dispatches, manual adjustments, and audit ledger.")

    t_id = st.session_state.tenant_id
    inv_df = DatabaseAdapter.read_df("""
        SELECT p.id, p.name, p.sku, p.unit, p.minimum_stock, p.purchase_price, IFNULL(i.current_stock, 0) as stock
        FROM products p LEFT JOIN inventory i ON p.id = i.product_id
        WHERE p.tenant_id = ?
    """, [t_id])

    total_stk = inv_df["stock"].sum() if not inv_df.empty else 0
    total_val = (inv_df["stock"] * inv_df["purchase_price"]).sum() if not inv_df.empty else 0
    low_stk = inv_df[inv_df["stock"] <= inv_df["minimum_stock"]].shape[0] if not inv_df.empty else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Cumulative Stock", f"{total_stk:,.1f}")
    m2.metric("Stock Valuation (Cost Basis)", format_currency(total_val))
    m3.metric("Low Stock Alerts", f"{low_stk} Item(s)")

    st.markdown("---")

    inv_tab1, inv_tab2, inv_tab3, inv_tab4, inv_tab5 = st.tabs(["📦 Stock Inward (+)", "📤 Stock Outward (-)", "⚖️ Adjust Stock", "📜 Movement History", "🗑️ Clear All Inventory"])
    product_options = inv_df["id"].tolist()
    product_fmt = lambda x: f"ID {x} — {inv_df[inv_df['id'] == x]['name'].values[0]} (Current: {inv_df[inv_df['id'] == x]['stock'].values[0]} {inv_df[inv_df['id'] == x]['unit'].values[0]})"

    with inv_tab1:
        st.markdown("#### 📦 Quick Stock Inward (+)")
        if product_options:
            with st.form("form_stock_in", clear_on_submit=True):
                si_pid = st.selectbox("Select Product", product_options, format_func=product_fmt)
                s_c1, s_c2 = st.columns(2)
                si_qty = s_c1.number_input("Quantity Received (+)", min_value=0.1, value=10.0, step=1.0)
                si_ref = s_c2.text_input("Reference / PO No.", placeholder="e.g. PO-9801")
                si_notes = st.text_input("Notes", placeholder="e.g. Warehouse receipt")

                if st.form_submit_button("📥 Confirm Stock In", type="primary", use_container_width=True):
                    ok, msg = adjust_inventory_stock(si_pid, si_qty, "Stock In", si_ref, si_notes)
                    if ok:
                        notify("Stock added successfully.", "success")
                        st.rerun()
                    else:
                        notify(msg, "error")
        else:
            st.info("No products registered. Add a product first.")

    with inv_tab2:
        st.markdown("#### 📤 Quick Stock Outward (-)")
        if product_options:
            with st.form("form_stock_out", clear_on_submit=True):
                so_pid = st.selectbox("Select Product", product_options, format_func=product_fmt)
                s_c1, s_c2 = st.columns(2)
                so_qty = s_c1.number_input("Quantity Deducted (-)", min_value=0.1, value=1.0, step=1.0)
                so_ref = s_c2.text_input("Reference / Dispatch No.", placeholder="e.g. DISP-4401")
                so_notes = st.text_input("Reason / Notes", placeholder="e.g. Branch transfer or sample")

                if st.form_submit_button("📤 Confirm Stock Out", type="primary", use_container_width=True):
                    ok, msg = adjust_inventory_stock(so_pid, -so_qty, "Stock Out", so_ref, so_notes)
                    if ok:
                        notify("Stock updated successfully.", "success")
                        st.rerun()
                    else:
                        notify(msg, "error")
        else:
            st.info("No products registered.")

    with inv_tab3:
        st.markdown("#### ⚖️ Stock Adjustment & Damage Entry")
        if product_options:
            with st.form("form_stock_adj", clear_on_submit=True):
                adj_pid = st.selectbox("Select Product", product_options, format_func=product_fmt)
                a1, a2 = st.columns(2)
                adj_type = a1.selectbox("Adjustment Reason", ["Manual Correction (+ / -)", "Damaged Stock (-)", "Customer Return (+)"])
                adj_qty = a2.number_input("Quantity Magnitude", min_value=0.1, value=1.0, step=1.0)
                adj_direction = st.radio("Direction", ["Increase Stock (+)", "Decrease Stock (-)"], horizontal=True) if "Manual Correction" in adj_type else None
                adj_notes = st.text_input("Audit Reason / Remarks*", placeholder="e.g. Physical inventory count discrepancy")

                if st.form_submit_button("⚖️ Post Stock Adjustment", type="primary", use_container_width=True):
                    if not adj_notes.strip():
                        notify("Audit reason is required for inventory adjustments.", "error")
                    else:
                        if "Damaged Stock" in adj_type:
                            delta = -adj_qty
                            op = "Damaged Stock"
                        elif "Customer Return" in adj_type:
                            delta = adj_qty
                            op = "Customer Return"
                        else:
                            delta = adj_qty if "Increase" in adj_direction else -adj_qty
                            op = "Stock Adjustment"

                        ok, msg = adjust_inventory_stock(adj_pid, delta, op, "ADJUSTMENT", adj_notes)
                        if ok:
                            notify("Stock adjustment saved successfully.", "success")
                            st.rerun()
                        else:
                            notify(msg, "error")
        else:
            st.info("No products registered.")

    with inv_tab4:
        st.markdown("#### 📜 Complete Inventory Movement Trail")
        movements_df = DatabaseAdapter.read_df("""
            SELECT m.id, m.date, p.name as product_name, p.sku, m.operation, m.quantity,
                   m.stock_before, m.stock_after, m.reference, m.notes
            FROM inventory_movements m JOIN products p ON m.product_id = p.id
            WHERE m.tenant_id = ?
            ORDER BY m.id DESC LIMIT 100
        """, [t_id])
        st.dataframe(movements_df, use_container_width=True, hide_index=True)

    with inv_tab5:
        st.markdown("#### 🗑️ Clear All Inventory Entries")
        st.warning("⚠️ **WARNING:** You are about to reset all inventory movement logs and set current stock quantities to zero.")
        clear_conf_inv = st.text_input("Type 'CLEAR ALL' to confirm inventory clearance", key="clear_inv_text")
        if st.button("Confirm Clear All Inventory", type="primary"):
            if clear_conf_inv.strip() == "CLEAR ALL":
                try:
                    DatabaseAdapter.execute("UPDATE inventory SET current_stock = 0.0 WHERE tenant_id = ?", (t_id,))
                    DatabaseAdapter.execute("DELETE FROM inventory_movements WHERE tenant_id = ?", (t_id,))
                    log_tenant_audit("CLEAR_ALL_INVENTORY", "Cleared all inventory movement records and reset stock.")
                    notify("All inventory entries have been cleared successfully.", "success")
                    st.rerun()
                except Exception as e:
                    notify(format_error(e), "error")
            else:
                notify("Confirmation text did not match 'CLEAR ALL'.", "error")


# =====================================================================
# MODULE 7: BARCODES
# =====================================================================
elif menu == "🏷️ Barcodes":
    st.title("Barcode & QR Code Generator")
    st.caption("Generate individual product barcodes or batch sheets ready for standard laser & inkjet printers.")

    t_id = st.session_state.tenant_id
    b_tab1, b_tab2 = st.tabs(["🏷️ Single Barcode / QR", "📦 Bulk Barcode Batch"])

    with b_tab1:
        products = DatabaseAdapter.execute("SELECT id, name, barcode, sku, selling_price FROM products WHERE tenant_id = ?", (t_id,), fetch="all") or []

        bc1, bc2 = st.columns(2)
        with bc1:
            sel_b_pid = st.selectbox("Select Catalog Product", [0] + [p["id"] for p in products], format_func=lambda x: "-- Custom Text / Manual Code --" if x == 0 else f"ID {x} — {next(p['name'] for p in products if p['id'] == x)} (SKU: {next(p['sku'] for p in products if p['id'] == x)})")
            def_code = next((p["barcode"] for p in products if p["id"] == sel_b_pid), "890123456789")
            bc_value = st.text_input("Barcode Data*", value=def_code)
            bc_type = st.selectbox("Symbology Standard", ["Code 128", "EAN-13", "EAN-8", "UPC-A", "Code 39", "QR Code"])

        with bc2:
            st.markdown("##### Live Barcode Preview")
            if bc_value:
                b_img = generate_barcode_image(bc_value, bc_type)
                if b_img:
                    st.image(b_img, caption=f"{bc_type} | {bc_value}")
                    buf = io.BytesIO()
                    b_img.save(buf, format="PNG")
                    st.download_button("📥 Download Barcode (PNG)", data=buf.getvalue(), file_name=f"barcode_{bc_value}.png", mime="image/png", type="primary")
                    notify("Barcode generated successfully.", "success")
                else:
                    notify("Could not render format. Verify data length and checksum.", "error")

    with b_tab2:
        st.markdown("#### 📦 Bulk Barcode Sheet Generator")
        st.caption("Select multiple items, define copy count, and generate an A4 PDF sheet.")

        p_df = DatabaseAdapter.read_df("SELECT id, name, sku, barcode FROM products WHERE tenant_id = ?", [t_id])
        if not p_df.empty:
            sel_pids = st.multiselect("Select Products for Sheet", p_df["id"].tolist(), format_func=lambda x: f"ID {x} — {p_df[p_df['id'] == x]['name'].values[0]}")
            b1, b2 = st.columns(2)
            bulk_qty = b1.number_input("Labels per Selected Product", min_value=1, max_value=200, value=12)
            bulk_btype = b2.selectbox("Symbology", ["Code 128", "EAN-13", "QR Code"], key="bulk_btype")

            if st.button("Generate Printable Barcode Sheet (PDF)", type="primary"):
                if sel_pids:
                    pdf_buf = io.BytesIO()
                    doc = SimpleDocTemplate(pdf_buf, pagesize=A4, rightMargin=15, leftMargin=15, topMargin=15, bottomMargin=15)
                    story = []
                    styles = getSampleStyleSheet()

                    story.append(Paragraph(f"<b>Barcode Sheet — {len(sel_pids) * bulk_qty} Total Labels</b>", styles['Normal']))
                    story.append(Spacer(1, 10))

                    grid_cells = []
                    for pid in sel_pids:
                        p_row = next(p for p in products if p["id"] == pid)
                        bimg = generate_barcode_image(p_row["barcode"], bulk_btype)
                        if bimg:
                            img_buf = io.BytesIO()
                            bimg.save(img_buf, format="PNG")
                            img_buf.seek(0)

                            for _ in range(bulk_qty):
                                cell_flow = [
                                    Paragraph(f"<font size=7><b>{p_row['name'][:18]}</b></font>", styles['Normal']),
                                    RLImage(img_buf, width=1.5 * inch, height=0.5 * inch),
                                    Paragraph(f"<font size=6>SKU: {p_row['sku']}</font>", styles['Normal'])
                                ]
                                grid_cells.append(cell_flow)

                    col_width = 1.8 * inch
                    rows_data = []
                    for i in range(0, len(grid_cells), 4):
                        chunk = grid_cells[i:i + 4]
                        while len(chunk) < 4:
                            chunk.append("")
                        rows_data.append(chunk)

                    if rows_data:
                        t_grid = Table(rows_data, colWidths=[col_width] * 4)
                        t_grid.setStyle(TableStyle([
                            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                            ('PADDING', (0, 0), (-1, -1), 4),
                        ]))
                        story.append(t_grid)

                    doc.build(story)
                    pdf_buf.seek(0)
                    st.download_button("📥 Download PDF Sheet", data=pdf_buf.getvalue(), file_name="barcode_batch.pdf", mime="application/pdf", type="primary")
                    notify("Barcode sheet generated successfully.", "success")
                else:
                    notify("Please select at least one product.", "warning")
        else:
            st.info("No products registered in catalog.")


# =====================================================================
# MODULE 8: LABEL DESIGNER
# =====================================================================
elif menu == "🎨 Label Designer":
    st.title("Product Label Designer")
    st.caption("Design price tags, retail stickers, and thermal barcode labels with instant live preview.")

    t_id = st.session_state.tenant_id
    settings = get_tenant_settings()

    l1, l2 = st.columns([1, 1.2])

    with l1:
        st.markdown("#### ⚙️ Label Setup")
        p_list = DatabaseAdapter.execute("SELECT * FROM products WHERE tenant_id = ?", (t_id,), fetch="all") or []
        sel_pid = st.selectbox("Choose Product to Label", [p["id"] for p in p_list], format_func=lambda x: f"ID {x} — {next(p['name'] for p in p_list if p['id'] == x)}") if p_list else None

        lbl_template = st.selectbox("Label Size Preset", ["Retail Price Tag (3 x 2 in)", "Standard Shelf Label (4 x 2 in)", "Compact Thermal Barcode (2 x 1 in)", "Custom Dimensions"])

        if lbl_template == "Retail Price Tag (3 x 2 in)":
            w_in, h_in = 3.0, 2.0
        elif lbl_template == "Standard Shelf Label (4 x 2 in)":
            w_in, h_in = 4.0, 2.0
        elif lbl_template == "Compact Thermal Barcode (2 x 1 in)":
            w_in, h_in = 2.0, 1.0
        else:
            w_in = st.number_input("Width (Inches)", min_value=1.0, max_value=8.0, value=3.0, step=0.5)
            h_in = st.number_input("Height (Inches)", min_value=0.5, max_value=8.0, value=2.0, step=0.5)

        with st.expander("Visible Elements on Label", expanded=True):
            show_biz = st.checkbox("Business Name", value=True)
            show_sku = st.checkbox("SKU Code", value=True)
            show_mrp = st.checkbox("MRP Price", value=True)
            show_sp = st.checkbox("Selling Price", value=True)
            show_gst = st.checkbox("GST Tax Text", value=True)
            show_hsn = st.checkbox("HSN Code", value=False)
            lbl_btype = st.selectbox("Barcode Standard", ["Code 128", "EAN-13", "QR Code"])

    with l2:
        st.markdown("#### 🖼️ Live Label Preview")
        if sel_pid:
            p_obj = next(p for p in p_list if p["id"] == sel_pid)
            opts = {
                "width_in": w_in,
                "height_in": h_in,
                "show_biz": show_biz,
                "show_sku": show_sku,
                "show_mrp": show_mrp,
                "show_sp": show_sp,
                "show_gst": show_gst,
                "show_hsn": show_hsn,
                "barcode_type": lbl_btype
            }

            lbl_img = render_label_preview(p_obj, settings, opts)
            st.image(lbl_img, caption=f"Print Size: {w_in} x {h_in} inches")

            buf = io.BytesIO()
            lbl_img.save(buf, format="PNG")
            st.download_button("📥 Download Label Image (PNG)", data=buf.getvalue(), file_name=f"label_{p_obj['sku']}.png", mime="image/png", type="primary", use_container_width=True)
            notify("Label saved successfully.", "success")
        else:
            st.info("No products registered. Add a product to design labels.")


# =====================================================================
# MODULE 9: REPORTS
# =====================================================================
elif menu == "📈 Reports":
    st.title("Business Intelligence & Financial Reports")
    st.caption("Instant profit analysis, GST summary, ledger accounts, and inventory positions.")

    t_id = st.session_state.tenant_id
    rep_menu = st.selectbox("Select Report", [
        "📊 Profit & Loss Analysis (Est. Gross Margin)",
        "🧾 GST Tax Liability Summary",
        "👥 Debitor & Creditor Balances",
        "📦 Stock Valuation & Safety Limits",
        "📜 Party Ledger Account Statements"
    ])

    st.markdown("---")

    if "Profit & Loss" in rep_menu:
        st.markdown("#### 📊 Profitability Breakdown")
        pnl_df = DatabaseAdapter.read_df("""
            SELECT bt.invoice_number, bt.invoice_date, bt.party_name, bi.item_name,
                   bi.quantity, bi.selling_price, p.purchase_price,
                   (bi.quantity * bi.selling_price) as sales_revenue,
                   (bi.quantity * p.purchase_price) as cost_of_goods,
                   (bi.quantity * (bi.selling_price - p.purchase_price)) as estimated_gross_profit
            FROM billing_items bi
            JOIN billing_transactions bt ON bi.transaction_id = bt.id
            JOIN products p ON bi.product_id = p.id
            WHERE bt.tenant_id = ? AND bt.transaction_type = 'SALE' AND bt.status = 'Active'
            ORDER BY bt.id DESC
        """, [t_id])

        if not pnl_df.empty:
            total_rev = pnl_df["sales_revenue"].sum()
            total_cogs = pnl_df["cost_of_goods"].sum()
            total_gp = pnl_df["estimated_gross_profit"].sum()
            gp_margin = (total_gp / total_rev * 100) if total_rev > 0 else 0.0

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Total Sales Revenue", format_currency(total_rev))
            r2.metric("Total Cost of Goods", format_currency(total_cogs))
            r3.metric("Est. Gross Profit", format_currency(total_gp))
            r4.metric("Gross Margin %", f"{gp_margin:.1f}%")

            st.dataframe(pnl_df, use_container_width=True, hide_index=True)
            notify("Report generated successfully.", "success")
        else:
            st.info("No sales recorded yet.")

    elif "GST Tax Liability" in rep_menu:
        st.markdown("#### 🧾 GST Tax Obligations")
        gst_sales = DatabaseAdapter.read_df("SELECT invoice_date, invoice_number, party_name, gstin, subtotal as taxable_value, gst_total as output_gst FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'SALE' AND status = 'Active'", [t_id])
        gst_purch = DatabaseAdapter.read_df("SELECT invoice_date, invoice_number, party_name, gstin, subtotal as taxable_value, gst_total as input_tax_credit FROM billing_transactions WHERE tenant_id = ? AND transaction_type = 'PURCHASE' AND status = 'Active'", [t_id])

        output_tax = gst_sales["output_gst"].sum() if not gst_sales.empty else 0.0
        itc_tax = gst_purch["input_tax_credit"].sum() if not gst_purch.empty else 0.0
        net_liability = output_tax - itc_tax

        g1, g2, g3 = st.columns(3)
        g1.metric("Output GST (Sales Tax Collected)", format_currency(output_tax))
        g2.metric("Input Tax Credit (Purchases)", format_currency(itc_tax))
        g3.metric("Net GST Payable / (Credit)", format_currency(net_liability))

        st.markdown("##### Outward Sales Tax Detail")
        st.dataframe(gst_sales, use_container_width=True, hide_index=True)

    elif "Debitor & Creditor" in rep_menu:
        st.markdown("#### 👥 Party Exposure & Balances")
        o1, o2 = st.columns(2)
        with o1:
            st.markdown("##### Debitors (Receivables Due)")
            deb_df = DatabaseAdapter.read_df("SELECT name, business_name, phone, balance as receivable_due FROM debitor_accounts WHERE tenant_id = ? AND balance > 0", [t_id])
            st.dataframe(deb_df, use_container_width=True, hide_index=True)
        with o2:
            st.markdown("##### Creditors (Payables Due)")
            cred_df = DatabaseAdapter.read_df("SELECT name, business_name, phone, balance as payable_due FROM creditor_accounts WHERE tenant_id = ? AND balance > 0", [t_id])
            st.dataframe(cred_df, use_container_width=True, hide_index=True)

    elif "Stock Valuation" in rep_menu:
        st.markdown("#### 📦 Stock Valuation & Safety Limits")
        stk_val_df = DatabaseAdapter.read_df("""
            SELECT p.id, p.name, p.sku, p.category, p.unit, p.purchase_price, p.selling_price,
                   IFNULL(i.current_stock, 0) as stock,
                   (IFNULL(i.current_stock, 0) * p.purchase_price) as valuation_at_cost,
                   p.minimum_stock,
                   CASE WHEN IFNULL(i.current_stock, 0) <= p.minimum_stock THEN '⚠️ Reorder' ELSE '✅ Healthy' END as status
            FROM products p LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.tenant_id = ?
            ORDER BY stock ASC
        """, [t_id])
        st.dataframe(stk_val_df, use_container_width=True, hide_index=True)

    elif "Party Ledger" in rep_menu:
        st.markdown("#### 📜 Party Ledger Statements")
        ptype = st.selectbox("Party Type", ["DEBITOR (Customer)", "CREDITOR (Supplier)"])
        actual_type = "DEBITOR" if "DEBITOR" in ptype else "CREDITOR"
        table = "debitor_accounts" if actual_type == "DEBITOR" else "creditor_accounts"
        parties = DatabaseAdapter.execute(f"SELECT name FROM {table} WHERE tenant_id = ?", (t_id,), fetch="all") or []
        p_names = [p["name"] for p in parties]

        if p_names:
            sel_party_led = st.selectbox("Select Account", p_names)
            ledger_df = DatabaseAdapter.read_df("""
                SELECT date, particular, debit, credit, balance, reference
                FROM ledgers WHERE tenant_id = ? AND party_type = ? AND party_name = ? ORDER BY id ASC
            """, [t_id, actual_type, sel_party_led])
            st.dataframe(ledger_df, use_container_width=True, hide_index=True)
        else:
            st.info(f"No registered {actual_type.lower()} accounts found.")


# =====================================================================
# MODULE 10: SETTINGS
# =====================================================================
elif menu == "⚙️ Settings":
    st.title("Settings & Security Hub")
    st.caption("Manage company identity, tax rules, trade parties, and password security.")

    t_id = st.session_state.tenant_id
    settings = get_tenant_settings()

    set_tab1, set_tab2, set_tab3 = st.tabs(["🏢 Business Profile & Invoicing", "👥 Customer & Supplier Accounts", "🔐 Account Password & Security"])

    with set_tab1:
        with st.form("settings_form"):
            st.markdown("#### 🏢 Company Identity & Tax")
            s1, s2 = st.columns(2)
            biz_name = s1.text_input("Business Name*", value=settings.get("business_name", ""))
            biz_gstin = s2.text_input("GSTIN Number*", value=settings.get("gstin", ""))

            s3, s4, s5 = st.columns(3)
            biz_phone = s3.text_input("Phone Number", value=settings.get("phone", ""))
            biz_email = s4.text_input("Email Address", value=settings.get("email", ""))
            biz_state = s5.text_input("State Jurisdiction", value=settings.get("state", "Maharashtra"))

            biz_addr = st.text_area("Registered Address", value=settings.get("address", ""))

            st.markdown("#### 🧾 Invoicing & Inventory Defaults")
            d1, d2, d3 = st.columns(3)
            inv_prefix = d1.text_input("Sales Invoice Prefix", value=settings.get("sales_prefix", "INV-"))
            pur_prefix = d2.text_input("Purchase Invoice Prefix", value=settings.get("purchase_prefix", "PUR-"))
            sku_prefix = d3.text_input("SKU Auto Prefix", value=settings.get("sku_prefix", "PRD-"))

            d4, d5 = st.columns(2)
            def_gst = d4.number_input("Default GST %", min_value=0.0, max_value=100.0, value=float(settings.get("default_gst", 18.0)))
            allow_neg = d5.checkbox("Allow Negative Stock (Permit outward billing at 0 stock)", value=bool(settings.get("allow_negative_stock", 0)))

            terms_txt = st.text_area("Invoice Terms & Conditions", value=settings.get("terms_and_conditions", ""))
            sig_txt = st.text_input("Signature Text", value=settings.get("signature_text", "Authorized Signatory"))

            if st.form_submit_button("💾 Save Settings", type="primary", use_container_width=True):
                gstin_ok = validate_gstin(biz_gstin)
                if not biz_name.strip():
                    notify("Business Name cannot be blank.", "error")
                elif not gstin_ok:
                    notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                else:
                    try:
                        DatabaseAdapter.execute("""
                            UPDATE business_settings SET
                                business_name = ?, gstin = ?, phone = ?, email = ?, state = ?, address = ?,
                                sales_prefix = ?, purchase_prefix = ?, sku_prefix = ?, default_gst = ?,
                                allow_negative_stock = ?, terms_and_conditions = ?, signature_text = ?
                            WHERE tenant_id = ?
                        """, (biz_name.strip(), biz_gstin.strip().upper(), biz_phone, biz_email, biz_state, biz_addr,
                              inv_prefix, pur_prefix, sku_prefix, def_gst, 1 if allow_neg else 0, terms_txt, sig_txt, t_id))
                        log_tenant_audit("SETTINGS_UPDATE", "Business profile updated.")
                        notify("Business settings updated successfully.", "success")
                        st.rerun()
                    except Exception as err:
                        notify(format_error(err), "error")

    with set_tab2:
        st.markdown("#### 👥 Trade Directory (Customers & Suppliers)")
        c_tab_deb, c_tab_cred = st.tabs(["Customers (Debitors)", "Suppliers (Creditors)"])

        with c_tab_deb:
            with st.form("add_debitor_settings_form", clear_on_submit=True):
                st.markdown("##### ➕ Register New Customer")
                cd1, cd2 = st.columns(2)
                d_name = cd1.text_input("Business Name*")
                d_biz = cd2.text_input("Business Sub-Name")
                cd3, cd4 = st.columns(2)
                d_phone = cd3.text_input("Mobile Number*")
                d_gst = cd4.text_input("GSTIN (e.g. 01ABCDE1234F1Z5)")

                if st.form_submit_button("Save Customer", type="primary"):
                    is_c_ok, e164_c, c_err = validate_international_phone("🇮🇳 India (+91)", d_phone)
                    gst_ok = validate_gstin(d_gst)
                    if not d_name.strip():
                        notify("Business Name is required.", "error")
                    elif not is_c_ok:
                        notify(c_err, "error")
                    elif not gst_ok:
                        notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                    else:
                        DatabaseAdapter.execute("""
                            INSERT INTO debitor_accounts (tenant_id, name, business_name, phone, gstin, balance)
                            VALUES (?, ?, ?, ?, ?, 0)
                            ON CONFLICT DO NOTHING
                        """, (t_id, d_name.strip(), d_biz.strip(), e164_c, d_gst.strip().upper()))
                        notify("Customer added successfully.", "success")
                        st.rerun()

            deb_list_df = DatabaseAdapter.read_df("SELECT id, name, business_name, phone, gstin, balance FROM debitor_accounts WHERE tenant_id = ? ORDER BY id DESC", [t_id])
            st.dataframe(deb_list_df, use_container_width=True, hide_index=True)

            if not deb_list_df.empty:
                st.markdown("---")
                with st.expander("🗑️ Delete Customer Account"):
                    sel_del_deb = st.selectbox("Select Customer to Delete", deb_list_df["id"].tolist(), format_func=lambda x: f"{deb_list_df[deb_list_df['id'] == x]['name'].values[0]}")
                    conf_del_d = st.checkbox("Confirm customer deletion", key=f"conf_del_d_{sel_del_deb}")
                    if st.button("Delete Customer", type="primary", disabled=not conf_del_d):
                        try:
                            DatabaseAdapter.execute("DELETE FROM debitor_accounts WHERE id = ? AND tenant_id = ?", (sel_del_deb, t_id))
                            notify("Customer deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")

        with c_tab_cred:
            with st.form("add_creditor_settings_form", clear_on_submit=True):
                st.markdown("##### ➕ Register New Supplier")
                cc1, cc2 = st.columns(2)
                c_name = cc1.text_input("Business Name*")
                cc3, cc4 = st.columns(2)
                c_phone = cc3.text_input("Mobile Number*")
                c_gst = cc4.text_input("GSTIN (e.g. 01ABCDE1234F1Z5)")

                if st.form_submit_button("Save Supplier", type="primary"):
                    is_p_ok, e164_p, p_err = validate_international_phone("🇮🇳 India (+91)", c_phone)
                    gst_ok = validate_gstin(c_gst)
                    if not c_name.strip():
                        notify("Business Name is required.", "error")
                    elif not is_p_ok:
                        notify(p_err, "error")
                    elif not gst_ok:
                        notify("Invalid GSTIN. Please enter a valid GSTIN.\nExample: 01ABCDE1234F1Z5", "error")
                    else:
                        DatabaseAdapter.execute("""
                            INSERT INTO creditor_accounts (tenant_id, name, business_name, phone, gstin, balance)
                            VALUES (?, ?, '', ?, ?, 0)
                            ON CONFLICT DO NOTHING
                        """, (t_id, c_name.strip(), e164_p, c_gst.strip().upper()))
                        notify("Supplier added successfully.", "success")
                        st.rerun()

            cred_list_df = DatabaseAdapter.read_df("SELECT id, name, business_name, phone, gstin, balance FROM creditor_accounts WHERE tenant_id = ? ORDER BY id DESC", [t_id])
            st.dataframe(cred_list_df, use_container_width=True, hide_index=True)

            if not cred_list_df.empty:
                st.markdown("---")
                with st.expander("🗑️ Delete Supplier Account"):
                    sel_del_cred = st.selectbox("Select Supplier to Delete", cred_list_df["id"].tolist(), format_func=lambda x: f"{cred_list_df[cred_list_df['id'] == x]['name'].values[0]}")
                    conf_del_c = st.checkbox("Confirm supplier deletion", key=f"conf_del_c_{sel_del_cred}")
                    if st.button("Delete Supplier", type="primary", disabled=not conf_del_c):
                        try:
                            DatabaseAdapter.execute("DELETE FROM creditor_accounts WHERE id = ? AND tenant_id = ?", (sel_del_cred, t_id))
                            notify("Supplier deleted successfully.", "success")
                            st.rerun()
                        except Exception as e:
                            notify(format_error(e), "error")

    with set_tab3:
        st.markdown("#### 🔐 Change Account Password")
        with st.form("pwd_change_form"):
            curr_p = st.text_input("Current Password*", type="password")
            new_p = st.text_input("New Strong Password*", type="password")
            strength_change = check_password_strength(new_p)
            render_password_checklist(strength_change)

            if st.form_submit_button("Update Password", type="primary"):
                user = DatabaseAdapter.execute("SELECT password_hash FROM users WHERE id = ?", (st.session_state.user_id,), fetch="one")
                if not user or not verify_password(curr_p, user["password_hash"]):
                    notify("Current password is incorrect.", "error")
                elif not strength_change["is_valid"]:
                    notify("New password does not meet the strong password requirements.", "error")
                else:
                    new_h = hash_password(new_p)
                    DatabaseAdapter.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_h, st.session_state.user_id))
                    log_tenant_audit("PASSWORD_CHANGE", "Account password updated.")
                    notify("Profile updated successfully.", "success")


# =====================================================================
# MODULE 11: SaaS SUPERADMIN PORTAL
# =====================================================================
elif menu == "🛡️ SaaS SuperAdmin":
    st.title("SaaS Owner Control Center")
    st.caption("System-wide visibility across all business tenants, active subscriptions, and cloud revenue.")

    if st.session_state.user_role != "SUPERADMIN":
        st.error("Access denied. SuperAdmin credentials required.")
        st.stop()

    tenants_df = DatabaseAdapter.read_df("SELECT * FROM tenants ORDER BY id DESC")
    total_tenants = len(tenants_df)
    active_tenants = len(tenants_df[tenants_df["subscription_status"] == "Active"]) if not tenants_df.empty else 0

    sa1, sa2, sa3 = st.columns(3)
    sa1.metric("Total Registered Businesses", total_tenants)
    sa2.metric("Active Subscriptions", active_tenants)
    sa3.metric("System Health", "Operational (Online)")

    st.markdown("---")
    st.markdown("#### 🏢 Registered Business Tenants")
    st.dataframe(tenants_df, use_container_width=True, hide_index=True)

    with st.expander("⚡ Manage Tenant Plan & Limits"):
        if not tenants_df.empty:
            sel_t = st.selectbox("Select Business", tenants_df["id"].tolist(), format_func=lambda x: f"Tenant {x} — {tenants_df[tenants_df['id'] == x]['business_name'].values[0]}")
            tp1, tp2 = st.columns(2)
            new_plan = tp1.selectbox("Subscription Plan", ["Free", "Basic", "Professional", "Enterprise"])
            new_stat = tp2.selectbox("Account Status", ["Active", "Suspended", "Expired"])

            if st.button("Update Tenant Subscription", type="primary"):
                DatabaseAdapter.execute("UPDATE tenants SET plan_tier = ?, subscription_status = ? WHERE id = ?", (new_plan, new_stat, sel_t))
                notify("Tenant subscription updated successfully.", "success")
                st.rerun()
