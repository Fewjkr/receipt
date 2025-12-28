import io
import os
import sqlite3
from datetime import datetime
from typing import Tuple

import pandas as pd
import streamlit as st
from PIL import Image

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# =========================
# Config
# =========================
st.set_page_config(page_title="Receipt / Purchase Order", layout="wide")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "receipt.db")
LOGO_PATH = os.path.join(APP_DIR, "assets", "logo.png")


# =========================
# Helpers: DB
# =========================
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS doc_counter (
                date_yyyymmdd TEXT PRIMARY KEY,
                counter INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipts (
                doc_no TEXT PRIMARY KEY,
                created_at TEXT,
                doc_type TEXT,
                company_name TEXT,
                company_address TEXT,
                company_tax_id TEXT,
                customer_name TEXT,
                customer_address TEXT,
                customer_tax_id TEXT,
                payment_method TEXT,
                note TEXT,
                subtotal REAL,
                discount REAL,
                shipping REAL,
                vat_rate REAL,
                vat_amount REAL,
                total REAL,
                currency TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipt_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_no TEXT,
                item_name TEXT,
                qty REAL,
                unit TEXT,
                unit_price REAL,
                line_total REAL,
                FOREIGN KEY(doc_no) REFERENCES receipts(doc_no)
            )
        """)
        conn.commit()

def next_doc_no(prefix: str) -> str:
    """
    prefix: e.g. "RC" or "PO"
    format: PREFIX-YYYYMMDD-#### (counter resets daily)
    """
    today = datetime.now().strftime("%Y%m%d")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT counter FROM doc_counter WHERE date_yyyymmdd = ?", (today,))
        row = cur.fetchone()
        if row is None:
            counter = 1
            cur.execute("INSERT INTO doc_counter(date_yyyymmdd, counter) VALUES(?, ?)", (today, counter))
        else:
            counter = row[0] + 1
            cur.execute("UPDATE doc_counter SET counter = ? WHERE date_yyyymmdd = ?", (counter, today))
        conn.commit()
    return f"{prefix}-{today}-{counter:04d}"

def save_receipt_to_db(
    doc_no: str,
    created_at: str,
    doc_type: str,
    company: dict,
    customer: dict,
    payment_method: str,
    note: str,
    items_df: pd.DataFrame,
    totals: dict,
    currency: str
):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO receipts(
                doc_no, created_at, doc_type,
                company_name, company_address, company_tax_id,
                customer_name, customer_address, customer_tax_id,
                payment_method, note,
                subtotal, discount, shipping, vat_rate, vat_amount, total, currency
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            doc_no, created_at, doc_type,
            company["name"], company["address"], company["tax_id"],
            customer["name"], customer["address"], customer["tax_id"],
            payment_method, note,
            totals["subtotal"], totals["discount"], totals["shipping"],
            totals["vat_rate"], totals["vat_amount"], totals["total"],
            currency
        ))

        cur.execute("DELETE FROM receipt_items WHERE doc_no = ?", (doc_no,))
        for _, r in items_df.iterrows():
            cur.execute("""
                INSERT INTO receipt_items(doc_no, item_name, qty, unit, unit_price, line_total)
                VALUES(?,?,?,?,?,?)
            """, (doc_no, str(r["สินค้า/รายละเอียด"]), float(r["จำนวน"]), str(r["หน่วย"]), float(r["ราคา/หน่วย"]), float(r["รวม"])))
        conn.commit()

def list_receipts(limit: int = 30) -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT doc_no, created_at, doc_type, customer_name, total, currency FROM receipts ORDER BY created_at DESC LIMIT ?",
            conn,
            params=(limit,)
        )
    return df

def load_receipt(doc_no: str) -> Tuple[dict, pd.DataFrame]:
    with get_conn() as conn:
        rec = pd.read_sql_query("SELECT * FROM receipts WHERE doc_no = ?", conn, params=(doc_no,))
        if rec.empty:
            raise ValueError("Document not found")
        r = rec.iloc[0].to_dict()
        items = pd.read_sql_query("SELECT item_name, qty, unit, unit_price, line_total FROM receipt_items WHERE doc_no = ?", conn, params=(doc_no,))
    return r, items


# =========================
# Helpers: Money
# =========================
def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)

def compute_totals(items_df: pd.DataFrame, discount: float, shipping: float, vat_rate: float):
    subtotal = float(items_df["รวม"].sum()) if not items_df.empty else 0.0
    discount = max(0.0, float(discount))
    shipping = max(0.0, float(shipping))
    vat_rate = max(0.0, float(vat_rate))

    taxable_base = max(0.0, subtotal - discount + shipping)
    vat_amount = taxable_base * (vat_rate / 100.0)
    total = taxable_base + vat_amount

    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "vat_rate": vat_rate,
        "vat_amount": vat_amount,
        "total": total,
        "taxable_base": taxable_base,
    }

def fmt_money(x: float, currency: str) -> str:
    # Keep simple; you can adjust formatting as you like
    return f"{x:,.2f} {currency}"


# =========================
# Helpers: PDF (ReportLab)
# =========================
def try_register_thai_font():
    """
    Optional: If you want Thai in PDF perfectly, put a Thai TTF in assets, e.g. assets/THSarabunNew.ttf
    Then register it. If not found, fallback to Helvetica.
    """
    ttf_path = os.path.join(APP_DIR, "assets", "THSarabunNew.ttf")
    if os.path.exists(ttf_path):
        try:
            pdfmetrics.registerFont(TTFont("THSarabun", ttf_path))
            return "THSarabun"
        except Exception:
            return "Helvetica"
    return "Helvetica"

def make_pdf(
    doc_no: str,
    created_at: str,
    doc_type: str,
    company: dict,
    customer: dict,
    payment_method: str,
    note: str,
    items_df: pd.DataFrame,
    totals: dict,
    currency: str,
    logo_path: str = None
) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    font_name = try_register_thai_font()
    c.setTitle(doc_no)

    # Margins
    left = 18 * mm
    right = width - 18 * mm
    top = height - 18 * mm

    # Header area
    y = top

    # Logo
    if logo_path and os.path.exists(logo_path):
        try:
            img = Image.open(logo_path)
            # keep ratio
            logo_w = 38 * mm
            ratio = img.height / img.width
            logo_h = logo_w * ratio
            c.drawImage(logo_path, left, y - logo_h, width=logo_w, height=logo_h, mask='auto')
        except Exception:
            pass

    # Title
    c.setFont(font_name, 18)
    title_map = {"RECEIPT": "ใบเสร็จรับเงิน / RECEIPT", "PO": "ใบสั่งซื้อ / PURCHASE ORDER"}
    title_text = title_map.get(doc_type, doc_type)
    c.drawRightString(right, y - 6 * mm, title_text)

    c.setFont(font_name, 11)
    c.drawRightString(right, y - 13 * mm, f"เลขที่เอกสาร / Doc No: {doc_no}")
    c.drawRightString(right, y - 19 * mm, f"วันที่ / Date: {created_at}")

    y = y - 30 * mm
    c.setStrokeColor(colors.lightgrey)
    c.line(left, y, right, y)
    y -= 10 * mm

    # Company / Customer blocks
    c.setFont(font_name, 12)
    c.drawString(left, y, "ผู้ขาย / Seller")
    c.drawString((left + right) / 2 + 5 * mm, y, "ผู้ซื้อ / Customer")
    y -= 6 * mm

    c.setFont(font_name, 11)
    # Seller
    c.drawString(left, y, company["name"])
    y2 = y - 5 * mm
    c.drawString(left, y2, company["address"])
    y3 = y2 - 5 * mm
    c.drawString(left, y3, f"Tax ID: {company['tax_id']}".strip())
    # Customer
    cx = (left + right) / 2 + 5 * mm
    c.drawString(cx, y, customer["name"])
    c.drawString(cx, y2, customer["address"])
    c.drawString(cx, y3, f"Tax ID: {customer['tax_id']}".strip())

    y = y3 - 10 * mm
    c.setStrokeColor(colors.lightgrey)
    c.line(left, y, right, y)
    y -= 8 * mm

    # Table header
    c.setFont(font_name, 11)
    col_x = [left, left + 85 * mm, left + 110 * mm, left + 135 * mm, right]
    headers = ["สินค้า/รายละเอียด", "จำนวน", "ราคา/หน่วย", "รวม", ""]
    c.setFillColor(colors.whitesmoke)
    c.rect(left, y - 6 * mm, right - left, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)

    c.drawString(col_x[0] + 2 * mm, y - 3 * mm, headers[0])
    c.drawRightString(col_x[2] - 2 * mm, y - 3 * mm, headers[1])
    c.drawRightString(col_x[3] - 2 * mm, y - 3 * mm, headers[2])
    c.drawRightString(col_x[4] - 2 * mm, y - 3 * mm, headers[3])

    y -= 10 * mm

    # Rows
    c.setFont(font_name, 11)
    row_h = 7 * mm
    max_rows_per_page = 20

    def draw_row(ypos, name, qty, unit, unit_price, line_total):
        # Item name (truncate if too long)
        name_txt = str(name)
        if len(name_txt) > 55:
            name_txt = name_txt[:55] + "..."
        c.drawString(col_x[0] + 2 * mm, ypos, name_txt)

        qty_txt = f"{qty:g} {unit}".strip()
        c.drawRightString(col_x[2] - 2 * mm, ypos, qty_txt)
        c.drawRightString(col_x[3] - 2 * mm, ypos, f"{unit_price:,.2f}")
        c.drawRightString(col_x[4] - 2 * mm, ypos, f"{line_total:,.2f}")

    if items_df.empty:
        draw_row(y, "-", 0, "", 0.0, 0.0)
        y -= row_h
    else:
        for i, r in items_df.reset_index(drop=True).iterrows():
            if i > 0 and i % max_rows_per_page == 0:
                c.showPage()
                y = top - 18 * mm
                c.setFont(font_name, 11)
            draw_row(y, r["สินค้า/รายละเอียด"], r["จำนวน"], r["หน่วย"], r["ราคา/หน่วย"], r["รวม"])
            y -= row_h

    y -= 6 * mm
    c.setStrokeColor(colors.lightgrey)
    c.line(left, y, right, y)
    y -= 10 * mm

    # Totals box (right side)
    box_w = 70 * mm
    box_x = right - box_w
    box_y = y - 40 * mm
    c.setFillColor(colors.whitesmoke)
    c.rect(box_x, box_y, box_w, 40 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)

    c.setFont(font_name, 11)
    ty = y - 6 * mm
    c.drawString(box_x + 3 * mm, ty, "Subtotal")
    c.drawRightString(right - 3 * mm, ty, f"{totals['subtotal']:,.2f}")
    ty -= 6 * mm
    c.drawString(box_x + 3 * mm, ty, "Discount")
    c.drawRightString(right - 3 * mm, ty, f"- {totals['discount']:,.2f}")
    ty -= 6 * mm
    c.drawString(box_x + 3 * mm, ty, "Shipping")
    c.drawRightString(right - 3 * mm, ty, f"{totals['shipping']:,.2f}")
    ty -= 6 * mm
    c.drawString(box_x + 3 * mm, ty, f"VAT ({totals['vat_rate']:.0f}%)")
    c.drawRightString(right - 3 * mm, ty, f"{totals['vat_amount']:,.2f}")
    ty -= 8 * mm
    c.setFont(font_name, 12)
    c.drawString(box_x + 3 * mm, ty, "TOTAL")
    c.drawRightString(right - 3 * mm, ty, f"{totals['total']:,.2f} {currency}")

    # Payment & Note (left side)
    c.setFont(font_name, 11)
    c.drawString(left, y - 6 * mm, f"ชำระโดย / Payment: {payment_method}")
    if note.strip():
        c.drawString(left, y - 12 * mm, f"หมายเหตุ / Note: {note.strip()[:90]}")

    # Footer
    c.setFont(font_name, 10)
    c.setFillColor(colors.grey)
    c.drawString(left, 14 * mm, "Generated by Streamlit Receipt App")
    c.setFillColor(colors.black)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# =========================
# UI
# =========================
init_db()

# Minimal CSS (แก้ “ขีดๆ/เส้นใต้” ให้ input ดูเนียนขึ้น + ปุ่มให้เป็นระเบียบ)
st.markdown(
    """
<style>
/* Make widgets spacing nicer */
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
/* Reduce weird underline feel in some themes */
input, textarea {outline: none !important;}
/* Buttons */
.stDownloadButton button, .stButton button {
  border-radius: 12px !important;
  padding: 0.55rem 0.9rem !important;
  font-weight: 600 !important;
}
/* Data editor */
[data-testid="stDataFrame"] {border-radius: 14px; overflow: hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# Header with logo
c1, c2 = st.columns([1, 3])
with c1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, use_container_width=True)
    else:
        st.caption("ใส่โลโก้ที่ assets/logo.png")
with c2:
    st.title("Receipt / Purchase Order (Streamlit)")
    st.caption("ออกใบเสร็จ/ใบสั่งซื้อ • เพิ่มรายการสินค้า • คำนวณ VAT/ส่วนลด/ค่าส่ง • ดาวน์โหลด PDF")

st.divider()

left, right = st.columns([2, 1])

with right:
    st.subheader("เอกสารล่าสุด")
    df_recent = list_receipts(limit=15)
    if df_recent.empty:
        st.info("ยังไม่มีเอกสารในระบบ")
    else:
        st.dataframe(df_recent, use_container_width=True, hide_index=True)

    st.markdown("### โหลดเอกสารเดิม")
    doc_to_load = st.text_input("ใส่ Doc No เพื่อโหลด", placeholder="เช่น RC-20251228-0001")
    if st.button("โหลด", use_container_width=True):
        try:
            rec, items = load_receipt(doc_to_load.strip())
            st.session_state["loaded_receipt"] = rec
            st.session_state["loaded_items"] = items
            st.success("โหลดสำเร็จ ✅")
        except Exception as e:
            st.error(f"โหลดไม่สำเร็จ: {e}")

with left:
    st.subheader("สร้างเอกสารใหม่")

    loaded = st.session_state.get("loaded_receipt")
    loaded_items = st.session_state.get("loaded_items")

    # Document type
    doc_type_label = st.selectbox(
        "ประเภทเอกสาร",
        ["ใบเสร็จรับเงิน (RECEIPT)", "ใบสั่งซื้อ (PO)"],
        index=0 if not loaded else (0 if loaded.get("doc_type") == "RECEIPT" else 1),
    )
    doc_type = "RECEIPT" if "RECEIPT" in doc_type_label else "PO"
    prefix = "RC" if doc_type == "RECEIPT" else "PO"

    colA, colB, colC = st.columns([1.2, 1, 1])
    with colA:
        if loaded:
            doc_no = st.text_input("Doc No", value=loaded.get("doc_no", ""), disabled=True)
        else:
            if "new_doc_no" not in st.session_state:
                st.session_state["new_doc_no"] = next_doc_no(prefix)
            doc_no = st.text_input("Doc No", value=st.session_state["new_doc_no"], disabled=True)

    with colB:
        created_at = st.text_input(
            "วันที่ / Date",
            value=(loaded.get("created_at") if loaded else datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    with colC:
        currency = st.selectbox("สกุลเงิน", ["THB", "USD"], index=0)

    st.markdown("### ข้อมูลผู้ขาย / Seller")
    s1, s2, s3 = st.columns([1.4, 2, 1])
    with s1:
        company_name = st.text_input("ชื่อร้าน/บริษัท", value=(loaded.get("company_name") if loaded else ""))
    with s2:
        company_address = st.text_input("ที่อยู่", value=(loaded.get("company_address") if loaded else ""))
    with s3:
        company_tax = st.text_input("Tax ID", value=(loaded.get("company_tax_id") if loaded else ""))

    st.markdown("### ข้อมูลลูกค้า / Customer")
    b1, b2, b3 = st.columns([1.4, 2, 1])
    with b1:
        customer_name = st.text_input("ชื่อลูกค้า", value=(loaded.get("customer_name") if loaded else ""))
    with b2:
        customer_address = st.text_input("ที่อยู่ลูกค้า", value=(loaded.get("customer_address") if loaded else ""))
    with b3:
        customer_tax = st.text_input("Tax ID ลูกค้า", value=(loaded.get("customer_tax_id") if loaded else ""))

    st.markdown("### รายการสินค้า")
    if loaded_items is not None and not loaded_items.empty:
        base_df = loaded_items.rename(
            columns={
                "item_name": "สินค้า/รายละเอียด",
                "qty": "จำนวน",
                "unit": "หน่วย",
                "unit_price": "ราคา/หน่วย",
                "line_total": "รวม",
            }
        )
    else:
        base_df = pd.DataFrame(
            [
                {"สินค้า/รายละเอียด": "ตัวอย่างสินค้า A", "จำนวน": 1, "หน่วย": "ชิ้น", "ราคา/หน่วย": 100.00, "รวม": 100.00},
                {"สินค้า/รายละเอียด": "ตัวอย่างสินค้า B", "จำนวน": 2, "หน่วย": "ชิ้น", "ราคา/หน่วย": 250.00, "รวม": 500.00},
            ]
        )

    edited = st.data_editor(
        base_df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "สินค้า/รายละเอียด": st.column_config.TextColumn(width="large"),
            "จำนวน": st.column_config.NumberColumn(step=1, min_value=0),
            "หน่วย": st.column_config.TextColumn(width="small"),
            "ราคา/หน่วย": st.column_config.NumberColumn(step=1, min_value=0, format="%.2f"),
            "รวม": st.column_config.NumberColumn(step=1, min_value=0, format="%.2f", disabled=True),
        },
        hide_index=True,
        key="items_editor",
    )

    # Recalculate line totals
    df = edited.copy()
    for col in ["จำนวน", "ราคา/หน่วย"]:
        df[col] = df[col].apply(lambda x: safe_float(x, 0.0))
    df["รวม"] = (df["จำนวน"] * df["ราคา/หน่วย"]).round(2)

    t1, t2, t3, t4 = st.columns([1, 1, 1, 1])
    with t1:
        discount = st.number_input("ส่วนลด (Discount)", min_value=0.0, value=float(loaded.get("discount", 0.0)) if loaded else 0.0, step=10.0)
    with t2:
        shipping = st.number_input("ค่าส่ง (Shipping)", min_value=0.0, value=float(loaded.get("shipping", 0.0)) if loaded else 0.0, step=10.0)
    with t3:
        vat_rate = st.number_input("VAT (%)", min_value=0.0, value=float(loaded.get("vat_rate", 7.0)) if loaded else 7.0, step=1.0)
    with t4:
        payment_method = st.selectbox("Payment", ["เงินสด (Cash)", "โอน (Transfer)", "บัตร (Card)", "อื่นๆ (Other)"], index=0)

    note = st.text_area("หมายเหตุ / Note", value=(loaded.get("note") if loaded else ""), height=80)

    totals = compute_totals(df, discount=discount, shipping=shipping, vat_rate=vat_rate)

    # Summary
    sA, sB, sC, sD = st.columns(4)
    sA.metric("Subtotal", fmt_money(totals["subtotal"], currency))
    sB.metric("Discount", fmt_money(totals["discount"], currency))
    sC.metric("VAT", fmt_money(totals["vat_amount"], currency))
    sD.metric("TOTAL", fmt_money(totals["total"], currency))

    st.divider()

    action1, action2, action3 = st.columns([1, 1, 1])
    with action1:
        if st.button("บันทึกลงระบบ (Save)", use_container_width=True):
            company = {"name": company_name, "address": company_address, "tax_id": company_tax}
            customer = {"name": customer_name, "address": customer_address, "tax_id": customer_tax}

            save_receipt_to_db(
                doc_no=doc_no,
                created_at=created_at,
                doc_type=doc_type,
                company=company,
                customer=customer,
                payment_method=payment_method,
                note=note,
                items_df=df,
                totals=totals,
                currency=currency,
            )
            st.success("บันทึกสำเร็จ ✅")

    with action2:
        if st.button("สร้าง PDF (Preview/Generate)", use_container_width=True):
            company = {"name": company_name, "address": company_address, "tax_id": company_tax}
            customer = {"name": customer_name, "address": customer_address, "tax_id": customer_tax}

            pdf_bytes = make_pdf(
                doc_no=doc_no,
                created_at=created_at,
                doc_type=doc_type,
                company=company,
                customer=customer,
                payment_method=payment_method,
                note=note,
                items_df=df,
                totals=totals,
                currency=currency,
                logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None,
            )
            st.session_state["pdf_bytes"] = pdf_bytes
            st.success("สร้าง PDF พร้อมดาวน์โหลด ✅")

    with action3:
        pdf_bytes = st.session_state.get("pdf_bytes")
        st.download_button(
            "ดาวน์โหลด PDF",
            data=pdf_bytes if pdf_bytes else b"",
            file_name=f"{doc_no}.pdf",
            mime="application/pdf",
            use_container_width=True,
            disabled=(pdf_bytes is None),
        )

    # Reset / New doc
    st.markdown("### เริ่มเอกสารใหม่")
    if st.button("สร้างเลขเอกสารใหม่ (New Document No.)", use_container_width=True):
        st.session_state.pop("loaded_receipt", None)
        st.session_state.pop("loaded_items", None)
        st.session_state.pop("pdf_bytes", None)
        st.session_state["new_doc_no"] = next_doc_no(prefix)
        st.success("สร้างเลขเอกสารใหม่แล้ว ✅")
        st.rerun()
