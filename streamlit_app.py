
import streamlit as st
import pandas as pd
from datetime import datetime
import io

# =========================
# Optional PDF Dependency
# =========================
HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
except Exception:
    HAS_REPORTLAB = False


# =========================
# Page config
# =========================
st.set_page_config(page_title="Receipt / Purchase Order", layout="wide")
st.title("🧾 Receipt / Purchase Order Generator")

# =========================
# Input section
# =========================
col1, col2 = st.columns(2)

with col1:
    company = st.text_input("Company / Seller", "Many Indicator Co.,Ltd.")
    customer = st.text_input("Customer", "Customer Name")
    doc_no = st.text_input("Document No", f"RC-{datetime.now().strftime('%Y%m%d')}-001")

with col2:
    date = st.date_input("Date", datetime.today())
    currency = st.selectbox("Currency", ["THB", "USD"])
    vat_rate = st.number_input("VAT %", value=7.0)

# =========================
# Items table
# =========================
st.subheader("Items")

df = st.data_editor(
    pd.DataFrame(
        [
            {"Item": "Product A", "Qty": 1, "Price": 100.0},
            {"Item": "Product B", "Qty": 2, "Price": 250.0},
        ]
    ),
    num_rows="dynamic",
    use_container_width=True,
)

df["Total"] = df["Qty"] * df["Price"]
subtotal = df["Total"].sum()
vat = subtotal * vat_rate / 100
grand_total = subtotal + vat

# =========================
# Summary
# =========================
c1, c2, c3 = st.columns(3)
c1.metric("Subtotal", f"{subtotal:,.2f} {currency}")
c2.metric("VAT", f"{vat:,.2f} {currency}")
c3.metric("Total", f"{grand_total:,.2f} {currency}")

# =========================
# Export functions
# =========================
def export_csv():
    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue().encode("utf-8")

def export_html():
    html = f"""
    <html>
    <head>
    <style>
    body {{ font-family: Arial; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #333; padding: 8px; }}
    th {{ background: #eee; }}
    </style>
    </head>
    <body>
    <h2>Receipt / Purchase Order</h2>
    <p><b>Doc No:</b> {doc_no}<br>
       <b>Date:</b> {date}<br>
       <b>Company:</b> {company}<br>
       <b>Customer:</b> {customer}</p>

    {df.to_html(index=False)}

    <h3>Total: {grand_total:,.2f} {currency}</h3>
    </body>
    </html>
    """
    return html.encode("utf-8")

def export_pdf():
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    text = c.beginText(40, 800)
    text.textLine(f"Receipt / Purchase Order")
    text.textLine(f"Doc No: {doc_no}")
    text.textLine(f"Company: {company}")
    text.textLine(f"Customer: {customer}")
    text.textLine(" ")
    for _, r in df.iterrows():
        text.textLine(f"{r['Item']}  {r['Qty']} x {r['Price']} = {r['Total']}")
    text.textLine(" ")
    text.textLine(f"TOTAL: {grand_total:,.2f} {currency}")
    c.drawText(text)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()

# =========================
# Download buttons
# =========================
st.subheader("📥 Export")

colA, colB, colC = st.columns(3)

with colA:
    st.download_button(
        "⬇️ Download CSV",
        data=export_csv(),
        file_name=f"{doc_no}.csv",
        mime="text/csv",
    )

with colB:
    st.download_button(
        "⬇️ Download HTML (Printable)",
        data=export_html(),
        file_name=f"{doc_no}.html",
        mime="text/html",
    )

with colC:
    if HAS_REPORTLAB:
        st.download_button(
            "⬇️ Download PDF",
            data=export_pdf(),
            file_name=f"{doc_no}.pdf",
            mime="application/pdf",
        )
    else:
        st.info("ℹ️ PDF requires reportlab (pip install reportlab)")

st.caption("✔ HTML สามารถเปิดแล้วสั่ง Print → Save as PDF ได้ทันที")
