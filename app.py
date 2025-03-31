
# ✅ File chuyển sang dùng PostgreSQL với SQLAlchemy
from fastapi import FastAPI, Request, Form, Query, Response, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, Column, String, Integer, Text
from pydantic import BaseModel
from uuid import uuid4
from typing import Optional
from pathlib import Path
import qrcode
import io
import os
import csv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import barcode
from barcode.writer import ImageWriter

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecret")
templates = Jinja2Templates(directory="templates")

DATABASE_URL = "postgresql+psycopg2://user:password@localhost/asset_db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

class AssetDB(Base):
    __tablename__ = "assets"
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    code = Column(String)
    category = Column(String)
    quantity = Column(Integer)
    description = Column(Text)

Base.metadata.create_all(bind=engine)

class Asset(BaseModel):
    id: str
    name: str
    code: str
    category: str
    quantity: int
    description: Optional[str] = ""

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path not in ("/login", "/static") and not request.session.get("user"):
        return RedirectResponse("/login")
    return await call_next(request)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == "admin" and password == "admin":
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Sai tài khoản hoặc mật khẩu"})

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")

@app.post("/add")
def add_asset(request: Request, name: str = Form(...), code: str = Form(...), category: str = Form(...), quantity: int = Form(...), description: str = Form(""), db=Depends(get_db)):
    new_id = str(uuid4())
    db_asset = AssetDB(id=new_id, name=name, code=code, category=category, quantity=quantity, description=description)
    db.add(db_asset)
    db.commit()
    generate_qr(db_asset)
    generate_barcode(db_asset)
    return RedirectResponse("/", status_code=302)

@app.get("/delete/{asset_id}")
def delete_asset(asset_id: str, db=Depends(get_db)):
    db.query(AssetDB).filter(AssetDB.id == asset_id).delete()
    db.commit()
    return RedirectResponse("/", status_code=302)

@app.get("/export/qr/{asset_id}")
def export_qr(asset_id: str, db=Depends(get_db)):
    asset = db.query(AssetDB).filter(AssetDB.id == asset_id).first()
    if not asset:
        return Response("Không tìm thấy thiết bị", status_code=404)
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    qr_path = f"data/qrcodes/{asset.id}.png"
    barcode_path = f"data/barcodes/{asset.id}.png"
    pdf.drawString(100, 800, f"Thiết bị: {asset.name}")
    pdf.drawImage(qr_path, 100, 600, width=150, height=150)
    pdf.drawImage(barcode_path, 300, 630, width=250, height=100)
    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return Response(buf.read(), media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={asset.code}_qr.pdf"})

@app.get("/export/csv")
def export_csv(db=Depends(get_db)):
    rows = db.query(AssetDB).all()
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["ID", "Tên", "Mã", "Danh mục", "Số lượng", "Ghi chú"])
    for r in rows:
        writer.writerow([r.id, r.name, r.code, r.category, r.quantity, r.description])
    stream.seek(0)
    return StreamingResponse(iter([stream.read()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=assets.csv"})

@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request, db=Depends(get_db)):
    data = db.query(AssetDB).all()
    total = len(data)
    total_qty = sum(x.quantity for x in data)
    avg = total_qty / total if total else 0
    max_item = max(data, key=lambda x: x.quantity, default=None)
    labels = [a.category for a in data]
    values = [a.quantity for a in data]
    return templates.TemplateResponse("stats.html", {"request": request, "total": total, "total_qty": total_qty, "avg": avg, "max_item": max_item, "labels": labels, "values": values})

@app.get("/categories")
def get_categories(db=Depends(get_db)):
    rows = db.query(AssetDB).all()
    return sorted(set(r.category for r in rows))

@app.get("/", response_class=HTMLResponse)
def home(request: Request, search: Optional[str] = Query(None), min_qty: Optional[int] = Query(None), max_qty: Optional[int] = Query(None), category: Optional[str] = Query(None), db=Depends(get_db)):
    query = db.query(AssetDB)
    if search:
        query = query.filter((AssetDB.name.ilike(f"%{search}%")) | (AssetDB.code.ilike(f"%{search}%")))
    if min_qty is not None:
        query = query.filter(AssetDB.quantity >= min_qty)
    if max_qty is not None:
        query = query.filter(AssetDB.quantity <= max_qty)
    if category:
        query = query.filter(AssetDB.category == category)
    assets = query.all()
    categories = sorted(set(a.category for a in db.query(AssetDB).all()))
    return templates.TemplateResponse("index.html", {"request": request, "assets": assets, "search": search or "", "min_qty": min_qty, "max_qty": max_qty, "category": category or "", "categories": categories})

def generate_qr(asset):
    img = qrcode.make(f"ID: {asset.id}\nTên: {asset.name}\nMã: {asset.code}\nDanh mục: {asset.category}")
    Path("data/qrcodes").mkdir(parents=True, exist_ok=True)
    img.save(f"data/qrcodes/{asset.id}.png")

def generate_barcode(asset):
    Path("data/barcodes").mkdir(parents=True, exist_ok=True)
    code128 = barcode.get("code128", asset.code, writer=ImageWriter())
    code128.save(f"data/barcodes/{asset.id}")
