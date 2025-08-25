from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import os
import zipfile
import json
import sqlite3
import random
import io
import textwrap
from reportlab.lib.utils import ImageReader
from email.mime.application import MIMEApplication

from sqlalchemy.exc import IntegrityError
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import mm
import io, os
from datetime import datetime, timedelta

from reportlab.pdfgen import canvas as rl_canvas
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, and_, or_, exists, func, desc, case 
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base, relationship
from werkzeug.security import generate_password_hash, check_password_hash

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'clave_muy_secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///negocios.db'


# Bind para clientes -> BD separada
app.config['SQLALCHEMY_BINDS'] = {
    'clientes': 'sqlite:///clientes.db'
}

db = SQLAlchemy(app)

# Base de datos secundaria para usuarios
user_db_path = 'usuarios.db'
engine_users = create_engine(f'sqlite:///{user_db_path}', connect_args={"check_same_thread": False})
db_users = scoped_session(sessionmaker(bind=engine_users))
BaseUsers = declarative_base()

# -----------------------------------------------------------------------------
# Filtros Jinja
# -----------------------------------------------------------------------------
@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return []

@app.template_filter('comerciales_str')
def comerciales_str(comerciales):
    """Recibe lista de ComercialNegocio y devuelve 'Carlos, Miguel' etc."""
    if not comerciales:
        return "—"
    nombres = []
    for c in comerciales:
        nombre = (c.comercial_nombre or "").strip()
        if not nombre:
            # fallback al local-part del email
            nombre = (c.comercial_email or "").split("@")[0]
        if nombre:
            nombres.append(nombre)
    return ", ".join(nombres) if nombres else "—"

def _sqlite_table_exists(db_path, table):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def _sqlite_column_exists(db_path, table, column):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    columns = [row[1] for row in cur.fetchall()]
    conn.close()
    return column in columns

def _sqlite_add_column(db_path, table, column, coltype):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")
    conn.commit()
    conn.close()
# -----------------------------------------------------------------------------
def tabla_existe(db_path, tabla):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tabla}';")
    existe = cursor.fetchone() is not None
    conn.close()
    return existe

def columna_existe(db_path, tabla, columna):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({tabla});")
    columnas = [col[1] for col in cursor.fetchall()]
    conn.close()
    return columna in columnas


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated
# --- GRUPOS (en usuarios.db) ---


from sqlalchemy import select

def negocios_visibles_para_usuario():
    # requiere session['user_email'], session['user_role'], session['user_id']
    email = session.get('user_email')
    rol   = session.get('user_role')
    uid   = session.get('user_id')

    q = Negocio.query

    if not email:
        return q.filter(False)  # vacío

    # 1) Admin ve todo
    if rol == 'admin':
        return q

    # 2) Si es "comercial": solo los suyos
    if rol == 'comercial':
        return (q.join(ComercialNegocio, isouter=True)
                 .filter(ComercialNegocio.comercial_email == email)
                 .distinct())

    # 3) ¿Es "jefe_grupo"? (rol textual) o ¿líder en algún grupo?
    es_jefe_por_rol = (rol == 'jefe_grupo')

    # buscar si lidera al menos un grupo
    lidera_algun_grupo = db_users.query(GrupoMiembro).filter_by(user_id=uid, es_lider=1).first() is not None

    if es_jefe_por_rol or lidera_algun_grupo:
        # obtener emails de todos los usuarios miembros de los grupos donde este user es líder
        # (si además tiene rol textual jefe_grupo, también aplicamos la lógica de grupos)
        grupos_lider = db_users.query(GrupoMiembro).filter_by(user_id=uid, es_lider=1).all()
        grupo_ids = [gm.grupo_id for gm in grupos_lider]

        if grupo_ids:
            # miembros de esos grupos
            miembros = (db_users.query(GrupoMiembro)
                        .filter(GrupoMiembro.grupo_id.in_(grupo_ids)).all())
            user_ids = list({m.user_id for m in miembros} | {uid})
            usuarios = db_users.query(User).filter(User.id.in_(user_ids)).all()
            emails_miembros = [u.email for u in usuarios if u.email]

            if emails_miembros:
                return (q.join(ComercialNegocio, isouter=True)
                         .filter(ComercialNegocio.comercial_email.in_(emails_miembros))
                         .distinct())

        # Si no tiene grupos creados pero tiene rol de jefe_grupo, por defecto solo ve los suyos:
        return (q.join(ComercialNegocio, isouter=True)
                 .filter(ComercialNegocio.comercial_email == email)
                 .distinct())

    # 4) Cualquier otro rol: no ve nada por comerciales
    return (q.join(ComercialNegocio, isouter=True)
             .filter(ComercialNegocio.comercial_email == email)
             .distinct())

# --- HELPER: correos de comerciales que dependen del jefe ---
def correos_miembros_del_jefe(jefe_user_id: int):
    """
    Devuelve lista de emails de TODOS los usuarios que pertenecen a algún
    grupo donde jefe_user_id es líder (incluye opcionalmente al propio jefe).
    """
    try:
        # grupos donde soy líder
        grupos_ids = [
            gm.grupo_id
            for gm in db_users.query(GrupoMiembro)
                              .filter(GrupoMiembro.user_id == jefe_user_id,
                                      GrupoMiembro.es_lider == 1)
                              .all()
        ]
        if not grupos_ids:
            return []

        # todos los miembros de esos grupos (miembros y líderes)
        miembros = (
            db_users.query(User)
                    .join(GrupoMiembro, GrupoMiembro.user_id == User.id)
                    .filter(GrupoMiembro.grupo_id.in_(grupos_ids))
                    .all()
        )
        # emails válidos
        emails = [u.email.strip().lower() for u in miembros if getattr(u, "email", None)]
        # uniq + fuera vacíos
        return sorted({e for e in emails if e})
    except Exception:
        return []

def query_clientes_visibles_para_usuario(base_query=None):
    """
    Visibilidad de clientes:
      - admin: todos
      - comercial: solo los creados por él (creado_por_email == su email)
      - jefe_grupo / lider_grupo: los creados por él + por cualquier miembro de sus grupos
      - otros: por defecto, solo los suyos por email
    """
    if base_query is None:
        base_query = Cliente.query

    role  = session.get('user_role')
    email = (session.get('user_email') or '').strip().lower()

    if role == 'admin':
        return base_query

    if role == 'comercial':
        return base_query.filter(func.lower(Cliente.creado_por_email) == email)

    if role in ('jefe_grupo', 'lider_grupo'):
        miembros = correos_miembros_del_jefe(session.get('user_id') or 0)  # ya la tienes
        correos_permitidos = {email} | set(miembros)
        if not correos_permitidos:
            correos_permitidos = {email}
        return base_query.filter(func.lower(Cliente.creado_por_email).in_(list(correos_permitidos)))

    # otros roles
    return base_query.filter(func.lower(Cliente.creado_por_email) == email)

# =========================
# CLIENTES (clientes.db)
# =========================
class ClienteEstadoCatalogo(db.Model):
    __bind_key__ = 'clientes'
    __tablename__ = 'cliente_estado_catalogo'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), unique=True, nullable=False)

class Cliente(db.Model):
    __tablename__ = "cliente"
    id = db.Column(db.Integer, primary_key=True)
    nombre_negocio = db.Column(db.String(200), nullable=False)
    estado = db.Column(db.String(20))  # "Hablado" | "Cerrado" | None
    observaciones = db.Column(db.Text)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
      # NUEVO: quién lo creó (usamos el email para casar con comerciales/grupos)
    creado_por_email = db.Column(db.String(120), index=True, nullable=True)
    creado_por_id    = db.Column(db.Integer, nullable=True)  # opcional, por si quieres guardar el id


    # relaciones
    direcciones = db.relationship('ClienteDireccion', backref='cliente',
                                  cascade='all, delete-orphan', lazy=True)

class ClienteDireccion(db.Model):
    __tablename__ = "cliente_direccion"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    calle = db.Column(db.String(255))
    municipio = db.Column(db.String(120))
    provincia = db.Column(db.String(120))
    principal = db.Column(db.Boolean, default=True)


class ClienteContacto(db.Model):
    __bind_key__ = 'clientes'
    __tablename__ = 'cliente_contacto'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    nombre = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(50))
    rol = db.Column(db.String(80))
    notas = db.Column(db.Text)


def _cliente_to_dict(c: Cliente):
    d = next((d for d in c.direcciones if d.principal) , None)
    return {
        "id": c.id,
        "nombre_negocio": c.nombre_negocio,
        "estado": c.estado,
        "observaciones": c.observaciones,
        "creado_en": c.creado_en.isoformat() if c.creado_en else None,
        "direccion": {
            "calle": d.calle if d else None,
            "municipio": d.municipio if d else None,
            "provincia": d.provincia if d else None,
        } if d else None
    }

def build_seguimiento_pdf(negocio, seguimiento, logo_path=None):
    """
    Genera un PDF (BytesIO) con la info del seguimiento.
    - Si el estado es 'riesgo', dibuja una banda/triángulo de alerta.
    """
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    W, H = letter

    # Margenes
    M = 36
    y = H - M

    # Encabezado
    # Logo (opcional)
    if logo_path and os.path.exists(logo_path):
        try:
            img = ImageReader(logo_path)
            c.drawImage(img, M, y-40, width=120, height=40, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    # Título
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.HexColor("#222222"))
    c.drawString(M, y-60, "Seguimiento de Negocio")

    # Si es riesgo, dibujar un triángulo rojo en la esquina superior derecha
    if (seguimiento.estado or "").lower() == "riesgo":
        c.setFillColor(colors.HexColor("#E53935"))
        tri_x = W - M - 60
        tri_y = H - M - 50
        # TRIÁNGULO (usar Path)
        path = c.beginPath()
        path.moveTo(tri_x, tri_y)           # punto 1
        path.lineTo(tri_x + 12, tri_y + 20) # punto 2
        path.lineTo(tri_x + 24, tri_y)      # punto 3
        path.close()
        c.drawPath(path, fill=1, stroke=0)

        # Texto ALERTA
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(tri_x - 4, tri_y - 12, "RIESGO")

    # Bloque de datos del negocio
    y -= 90
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)

    def draw_kv(label, value, x, y):
        c.setFillColor(colors.HexColor("#6b7280"))
        c.setFont("Helvetica", 9)
        c.drawString(x, y, label)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y-14, value if value else "—")
        return y - 30

    com_str = ", ".join(
        [(cn.comercial_nombre or cn.comercial_email or "") for cn in (negocio.comerciales or [])]
    ) or "—"

    y = draw_kv("Negocio", f"{negocio.nombre} (ID {negocio.id})", M, y)
    y = draw_kv("Propietario", negocio.propietario or "—", M, y)
    y = draw_kv("Administrador", negocio.admin or "—", M, y)
    y = draw_kv("Dirección", negocio.direccion or "—", M, y)
    y = draw_kv("Comerciales", com_str, M, y)

    # Línea divisoria
    c.setStrokeColor(colors.HexColor("#e5e7eb"))
    c.line(M, y, W - M, y)
    y -= 18

    # Metadatos del seguimiento
    c.setFillColor(colors.HexColor("#6b7280"))
    c.setFont("Helvetica", 9)
    c.drawString(M, y, "Fecha seguimiento")
    c.drawString(M + 220, y, "Creado por")
    c.drawString(M + 400, y, "Estado")

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M, y-14, seguimiento.creado_en.strftime("%d/%m/%Y %H:%M"))
    c.drawString(M + 220, y-14, seguimiento.creado_por_email or "—")
    c.drawString(M + 400, y-14, (seguimiento.estado or "—").upper())
    y -= 34

    # Observación (texto largo con wrap)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "Observación")
    y -= 16

    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#111827"))

    wrapper = textwrap.TextWrapper(width=98)  # ajusta a tu gusto
    obs_lines = wrapper.wrap(seguimiento.observacion or "")
    # caja suave
    c.setFillColor(colors.HexColor("#f6f7fb"))
    box_h = max(40, 14 * (len(obs_lines) + 1))
    c.roundRect(M, y - box_h, W - 2*M, box_h, 8, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#111827"))

    ty = y - 16
    for ln in obs_lines:
        c.drawString(M + 10, ty, ln)
        ty -= 14
    y -= (box_h + 10)

    # Pie
    c.setFillColor(colors.HexColor("#9ca3af"))
    c.setFont("Helvetica", 8)
    c.drawRightString(W - M, M + 6, "Generado automáticamente por el sistema")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

def _cliente_to_dict(c: Cliente):
    d = next((d for d in c.direcciones if d.principal) , None)
    return {
        "id": c.id,
        "nombre_negocio": c.nombre_negocio,
        "estado": c.estado,
        "observaciones": c.observaciones,
        "creado_en": c.creado_en.isoformat() if c.creado_en else None,
        "direccion": {
            "calle": d.calle if d else None,
            "municipio": d.municipio if d else None,
            "provincia": d.provincia if d else None,
        } if d else None
    }

def query_negocios_visibles_para_usuario(base_query=None):
    """
    Filtra Negocio según el rol actual en sesión:
      - admin: todos
      - comercial: solo los suyos (por email o por nombre)
      - jefe_grupo / lider_grupo: los suyos + los de todos los miembros de sus grupos
      - otros: por defecto, solo los suyos por email
    """
    if base_query is None:
        base_query = Negocio.query

    role  = session.get('user_role')
    email = (session.get('user_email') or '').strip().lower()

    user_obj = db_users.query(User).get(session.get('user_id')) if session.get('user_id') else None
    nombre_usuario = (user_obj.nombre_completo or '').strip() if user_obj else None

    # Admin
    if role == 'admin':
        return base_query

    # Comercial
    if role == 'comercial':
        return (base_query
                .join(ComercialNegocio, isouter=True)
                .filter(or_(
                    func.lower(ComercialNegocio.comercial_email) == email,
                    and_(nombre_usuario != None, ComercialNegocio.comercial_nombre == nombre_usuario)
                ))
                .distinct())

    # Jefe/Líder de grupo
    if role in ('jefe_grupo', 'lider_grupo'):
        miembros = correos_miembros_del_jefe(session.get('user_id') or 0)
        # siempre incluir su propio correo
        correos_permitidos = {email} | set(miembros)
        if not correos_permitidos:
            correos_permitidos = {email}  # fallback
        return (base_query
                .join(ComercialNegocio, isouter=True)
                .filter(func.lower(ComercialNegocio.comercial_email).in_(list(correos_permitidos)))
                .distinct())

    # Otros roles: por defecto, solo lo asignado a su email
    return (base_query
            .join(ComercialNegocio, isouter=True)
            .filter(func.lower(ComercialNegocio.comercial_email) == email)
            .distinct())


def migrate_clientes_autor():
    db_path = 'negocios.db'
    if not _sqlite_table_exists(db_path, 'cliente'):
        return
    if not _sqlite_column_exists(db_path, 'cliente', 'creado_por_email'):
        _sqlite_add_column(db_path, 'cliente', 'creado_por_email', 'TEXT')
    if not _sqlite_column_exists(db_path, 'cliente', 'creado_por_id'):
        _sqlite_add_column(db_path, 'cliente', 'creado_por_id', 'INTEGER')


# =========================
# DASHBOARD
# =========================

@app.get("/dashboard")
@login_required
def dashboard_view():
    # Comerciales para el filtro (opcional)
    rol_com = db_users.query(Role).filter_by(name='comercial').first()
    comerciales = db_users.query(User).filter(User.role == rol_com)\
                    .order_by(User.nombre_completo, User.email).all() if rol_com else []
    return render_template("admin/dashboard.html", comerciales=comerciales)



@app.route("/api/clientes", methods=["GET"])
def api_clientes_list():
    """
    Devuelve JSON con los clientes visibles para el usuario actual,
    ordenados por fecha de creación (desc).
    """
    query = query_clientes_visibles_para_usuario(
        Cliente.query.order_by(Cliente.creado_en.desc())
    )
    clientes = query.all()
    return jsonify({"success": True, "items": [_cliente_to_dict(c) for c in clientes]})

@app.route("/api/clientes", methods=["POST"])
def api_clientes_create():
    """
    Crea un cliente y lo liga al usuario logueado (creado_por_email / creado_por_id).
    Espera JSON como:
    {
      "nombre_negocio": "Texto",
      "estado": "Hablado" | "Cerrado" | null,
      "observaciones": "texto",             # opcional
      "direccion": {                        # opcional
          "calle": "Av. 1",
          "municipio": "Centro",
          "provincia": "Pinar"
      }
    }
    """
    data = request.get_json(force=True) or {}

    nombre = (data.get("nombre_negocio") or "").strip()
    if not nombre:
        return jsonify(success=False, error="Nombre requerido"), 400

    autor_email = (session.get('user_email') or '').strip().lower()
    autor_id    = session.get('user_id')

    c = Cliente(
        nombre_negocio=nombre,
        estado=(data.get("estado") or None),
        observaciones=(data.get("observaciones") or None),
        creado_por_email=(autor_email or None),
        creado_por_id=(autor_id or None)
    )
    db.session.add(c)
    db.session.flush()  # para tener c.id

    # Dirección principal (opcional)
    dir_data = data.get("direccion") or {}
    if dir_data:
        d = ClienteDireccion(
            cliente_id=c.id,
            calle=(dir_data.get("calle") or None),
            municipio=(dir_data.get("municipio") or None),
            provincia=(dir_data.get("provincia") or None),
            principal=True
        )
        db.session.add(d)

    db.session.commit()
    return jsonify(success=True, item=_cliente_to_dict(c)), 201

@app.route("/api/clientes/<int:cid>", methods=["GET"])
def api_clientes_detail(cid):
    c = Cliente.query.get_or_404(cid)
    return jsonify(success=True, item=_cliente_to_dict(c))

@app.route("/api/clientes/<int:cid>", methods=["PUT"])
def api_clientes_update(cid):
    c = Cliente.query.get_or_404(cid)
    data = request.get_json(force=True)
    if "nombre_negocio" in data:
        n = (data.get("nombre_negocio") or "").strip()
        if not n: return jsonify(success=False, error="Nombre requerido"), 400
        c.nombre_negocio = n
    if "estado" in data:
        c.estado = (data.get("estado") or None)
    if "observaciones" in data:
        c.observaciones = (data.get("observaciones") or None)

    d = next((d for d in c.direcciones if d.principal), None)
    if not d:
        d = ClienteDireccion(cliente_id=c.id, principal=True)
        db.session.add(d)
    dir_data = data.get("direccion") or {}
    if dir_data:
        d.calle = (dir_data.get("calle") or None)
        d.municipio = (dir_data.get("municipio") or None)
        d.provincia = (dir_data.get("provincia") or None)

    db.session.commit()
    return jsonify(success=True, item=_cliente_to_dict(c))

@app.route("/api/clientes/<int:cid>", methods=["DELETE"])
def api_clientes_delete(cid):
    c = Cliente.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify(success=True)
# ================= FIN CLIENTES =================
# -----------------------------------------------------------------------------
# Modelo Usuarios/Roles (usuarios.db)
# -----------------------------------------------------------------------------
class Role(BaseUsers):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    users = relationship('User', back_populates='role')

class User(BaseUsers):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role_id = Column(Integer, ForeignKey('roles.id'))
    role = relationship('Role', back_populates='users')
    nombre_completo = Column(String(150))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

def crear_roles_iniciales():
    roles_necesarios = ['admin', 'comercial', 'asistente', 'usuario','jefe_grupo']
    for rol_nombre in roles_necesarios:
        rol_existente = db_users.query(Role).filter_by(name=rol_nombre).first()
        if not rol_existente:
            db_users.add(Role(name=rol_nombre))
    db_users.commit()

def seed_comerciales():
    """Crea 8 cuentas comerciales (si no existen) con pass 123456."""
    nombres = ["Carlos", "Orlando", "Kevin", "Miguel", "David", "Adrian", "Ibrain", "Jayme"]
    rol_com = db_users.query(Role).filter_by(name='comercial').first()
    if not rol_com:
        rol_com = Role(name='comercial')
        db_users.add(rol_com)
        db_users.commit()

    for n in nombres:
        email = f"{n.lower()}@gmail.com"
        u = db_users.query(User).filter_by(email=email).first()
        if not u:
            u = User(email=email, role=rol_com, nombre_completo=n)
            u.set_password("123456")
            db_users.add(u)
    db_users.commit()

# Inicializa usuarios.db
if not os.path.exists(user_db_path):
    BaseUsers.metadata.create_all(bind=engine_users)
    crear_roles_iniciales()
    # admin inicial
    admin_role = db_users.query(Role).filter_by(name='admin').first()
    admin_user = User(email='admin@admin.com', role=admin_role)
    admin_user.set_password('admin')
    db_users.add(admin_user)
    db_users.commit()
else:
    if not tabla_existe(user_db_path, 'users'):
        BaseUsers.metadata.create_all(bind=engine_users)
        crear_roles_iniciales()
    if not columna_existe(user_db_path, 'users', 'nombre_completo'):
        conn = sqlite3.connect(user_db_path)
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE users ADD COLUMN nombre_completo TEXT;")
        conn.commit()
        conn.close()


def get_group_leaders_emails_for_user(member_user_id: int):
    """Devuelve emails de líderes de los grupos a los que pertenece member_user_id."""
    # grupos del miembro
    grupos_ids = [
        gm.grupo_id for gm in db_users.query(GrupoMiembro).filter_by(user_id=member_user_id).all()
    ]
    if not grupos_ids:
        return []
    # líderes de esos grupos
    lideres = (
        db_users.query(User)
        .join(GrupoMiembro, GrupoMiembro.user_id == User.id)
        .filter(GrupoMiembro.grupo_id.in_(grupos_ids), GrupoMiembro.es_lider == 1)
        .all()
    )
    return [u.email for u in lideres if u.email]

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "lorastewe08@gmail.com"              # mismo que FROM_EMAIL
SMTP_PASS = "bbiimdctxnqhgyuy"                 # APP PASSWORD (16 chars)
FROM_EMAIL = "lorastewe08@gmail.com"
FROM_NAME  = "Atreus Notifier"

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from markupsafe import escape
from datetime import datetime
import smtplib

def send_mail_simple(to_list, subject, body_html, body_text=None, attachments=None):
    """
    Envía correo (texto/HTML) y adjunta archivos opcionalmente.
    attachments: lista de tuplas (filename, bytes_content, mimetype)
    """
    if not isinstance(to_list, (list, tuple)):
        to_list = [to_list]

    body_text = body_text or (body_html or "").replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')

    msg = MIMEMultipart("mixed")  # mixed para permitir adjuntos
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = ", ".join(to_list)

    # parte alternativa (texto + html)
    alt = MIMEMultipart("alternative")
    if body_text:
        alt.attach(MIMEText(body_text, "plain", _charset="utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html",  _charset="utf-8"))
    msg.attach(alt)

    # adjuntos
    attachments = attachments or []
    for fname, data_bytes, mimetype in attachments:
        part = MIMEApplication(data_bytes, _subtype=(mimetype.split("/")[-1] if "/" in mimetype else None))
        part.add_header('Content-Disposition', 'attachment', filename=fname)
        msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo(); server.starttls(); server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_list, msg.as_string())
        return True
    except Exception as e:
        print("Error enviando correo:", e)
        return False


class Grupo(BaseUsers):
    __tablename__ = 'grupos'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)

    miembros = relationship('GrupoMiembro', back_populates='grupo', cascade='all, delete-orphan')

class GrupoMiembro(BaseUsers):
    __tablename__ = 'grupo_miembros'
    id = Column(Integer, primary_key=True)
    grupo_id = Column(Integer, ForeignKey('grupos.id'), nullable=False)
    user_id  = Column(Integer, ForeignKey('users.id'), nullable=False)
    es_lider = Column(Integer, default=0)  # 1 = líder, 0 = miembro normal

    grupo = relationship('Grupo', back_populates='miembros')
    user  = relationship('User')

# Crear tablas si no existen
BaseUsers.metadata.create_all(bind=engine_users)
# -----------------------------------------------------------------------------
# Modelos App (negocios.db)
# -----------------------------------------------------------------------------
class Negocio(db.Model):
    __tablename__ = 'negocio'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)

    tipo_negocio = db.Column(db.String(20), default='negocio')
    tipo = db.Column(db.String(50), default='negocio')
    padre_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=True)
    hijos = db.relationship('Negocio', backref=db.backref('padre', remote_side=[id]), lazy=True)

    telefonos_extras = db.Column(db.Text, nullable=True)
    propietario = db.Column(db.String(100), nullable=False)
    admin = db.Column(db.String(100), nullable=True)
    tel_propietario = db.Column(db.String(20))
    tel_admin = db.Column(db.String(20), nullable=True)
    direccion = db.Column(db.String(200))
    negocios_hijos = db.Column(db.String(300))
    proveedor_id = db.Column(db.Integer, db.ForeignKey('proveedor_internet.id'), nullable=True)
    proveedor    = db.relationship('ProveedorInternet', lazy=True)

    # Campo legado (texto). Lo dejamos por compatibilidad, pero la vista usa la tabla de vínculo
    comercial = db.Column(db.String(100))

    observacion = db.Column(db.Text, nullable=True)
    licencia = db.Column(db.String(20))
    moneda_licencia = db.Column(db.String(3), default='USD')
    conectividad = db.Column(db.Float, default=0.0)
    direcciones = db.relationship('Direccion', backref='negocio',
                                  cascade='all, delete-orphan', lazy=True)

    # relación con comerciales (tabla simple)
    comerciales = db.relationship(
        'ComercialNegocio',
        back_populates='negocio',
        cascade='all, delete-orphan',
        lazy=True
    )

class ComercialNegocio(db.Model):
    __tablename__ = 'comercial_negocio'
    id = db.Column(db.Integer, primary_key=True)
    negocio_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=False)
    comercial_nombre = db.Column(db.String(150))
    comercial_email = db.Column(db.String(120))

    negocio = db.relationship('Negocio', back_populates='comerciales')

class Direccion(db.Model):
    __tablename__ = 'direccion'
    id = db.Column(db.Integer, primary_key=True)
    calle = db.Column(db.String(255), nullable=False)
    calle2 = db.Column(db.String(255))
    ciudad = db.Column(db.String(100), nullable=False)
    cp = db.Column(db.String(20))
    pais = db.Column(db.String(100))
    provincia = db.Column(db.String(100), nullable=False)
    municipio = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text)
    principal = db.Column(db.Boolean, default=False)

    negocio_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=False)


class Contacto(db.Model):
    __tablename__ = 'contacto'
    id = db.Column(db.Integer, primary_key=True)
    negocio_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=False)

    nombre = db.Column(db.String(120), nullable=False)
    cargo = db.Column(db.String(120))
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notas = db.Column(db.Text)
    principal = db.Column(db.Boolean, default=False)

# en tu modelo Negocio añade la relación:
# (déjalo junto a direcciones / comerciales)
Negocio.contactos = db.relationship(
    'Contacto',
    backref='negocio',
    cascade='all, delete-orphan',
    lazy=True
)




# -----------------------------------------------------------------------------
# Helpers SQLite (migración mínima)
# -----------------------------------------------------------------------------

class ProveedorInternet(db.Model):
    __tablename__ = 'proveedor_internet'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), unique=True, nullable=False)
    descripcion = db.Column(db.Text)

class Modulo(db.Model):
    __tablename__ = 'modulo'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), unique=True, nullable=False)
    descripcion = db.Column(db.Text)

class NegocioModulo(db.Model):
    __tablename__ = 'negocio_modulo'
    id = db.Column(db.Integer, primary_key=True)
    negocio_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=False)
    modulo_id = db.Column(db.Integer, db.ForeignKey('modulo.id'), nullable=False)
    enabled = db.Column(db.Boolean, default=False)

    negocio = db.relationship('Negocio', backref=db.backref('mods_rel', cascade='all, delete-orphan'))
    modulo = db.relationship('Modulo')


# relación en Negocio (después de la clase Negocio)

# Utilidades SQLite (usuarios.db)

# =========================================
# Rutas de CLIENTES (todo en app.py)
# =========================================
class Seguimiento(db.Model):
    __tablename__ = "seguimiento"
    id = db.Column(db.Integer, primary_key=True)
    negocio_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=False)
    observacion = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), nullable=False, default='activo')  # 'activo' | 'riesgo'
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    creado_por_email = db.Column(db.String(120), index=True, nullable=True)
    creado_por_id = db.Column(db.Integer, index=True, nullable=True)

# relación opcional si quieres acceso rápido desde Negocio
Negocio.seguimientos = db.relationship(
    'Seguimiento',
    backref='negocio',
    cascade='all, delete-orphan',
    order_by=Seguimiento.creado_en.desc(),
    lazy=True
)

# -----------------------------
# CLIENTES: lista, crear, detalle
# -----------------------------
@app.get('/clientes', endpoint='clientes_lista')
def clientes_lista():
    """
    Lista paginada de clientes según visibilidad:
      - admin: todos
      - comercial: solo creados por él
      - jefe_grupo / lider_grupo: los suyos + los de sus miembros
    """
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = 12

    # aplica helper de visibilidad y ordena por fecha desc
    query = query_clientes_visibles_para_usuario(
        Cliente.query.order_by(Cliente.creado_en.desc())
    )

    total = query.count()
    clientes = (query
                .offset((pagina - 1) * por_pagina)
                .limit(por_pagina)
                .all())
    total_paginas = (total + por_pagina - 1) // por_pagina

    # (si usas catálogo de estados)
    estados = ClienteEstadoCatalogo.query.order_by(ClienteEstadoCatalogo.nombre).all()

    return render_template(
        'clientes/lista_clientes.html',
        clientes=clientes,
        estados=estados,
        pagina=pagina,
        total_paginas=total_paginas
    )


@app.get("/negocio/<int:negocio_id>/seguimientos")
def listar_seguimientos(negocio_id):
    negocio = Negocio.query.get_or_404(negocio_id)
    data = [{
        "id": s.id,
        "observacion": s.observacion,
        "estado": s.estado,
        "creado_en": s.creado_en.strftime("%d/%m/%Y %H:%M") if s.creado_en else "",
        "creado_por_email": s.creado_por_email
    } for s in negocio.seguimientos]
    return jsonify(success=True, items=data)

@app.post("/negocio/<int:negocio_id>/seguimientos")
def crear_seguimiento(negocio_id):
    negocio = Negocio.query.get_or_404(negocio_id)
    payload = request.get_json(silent=True) or request.form

    obs = (payload.get("observacion") or "").strip()
    estado = (payload.get("estado") or "activo").strip().lower()
    if estado not in ("activo", "riesgo"):
        estado = "activo"

    if not obs:
        return jsonify(success=False, error="La observación es obligatoria"), 400

    uid    = session.get('user_id')
    uemail = session.get('user_email')

    seg = Seguimiento(
        negocio_id=negocio.id,
        observacion=obs,
        estado=estado,
        creado_por_email=uemail,
        creado_por_id=uid
    )
    db.session.add(seg)
    db.session.commit()

    # === Notificación solo si es RIESGO ===
    if estado == "riesgo":
        # destinatarios: creador + jefes de grupo del creador
        destinatarios = set()
        if uemail:
            destinatarios.add(uemail)
        if uid:
            for mail in get_group_leaders_emails_for_user(uid):
                if mail:
                    destinatarios.add(mail)

        # Datos para el correo / pdf
        com_str  = comerciales_str(negocio.comerciales)
        dir_text = negocio.direccion or "—"
        fecha    = seg.creado_en.strftime('%d/%m/%Y %H:%M')
        asunto   = f"🚨 Seguimiento en riesgo: {negocio.nombre}"

        # Cuerpo HTML breve (el PDF trae el formato principal)
        body_html = f"""
        <div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5;color:#2d3748">
          <h2 style="margin:0 0 12px;font-size:18px">Alerta de riesgo</h2>
          <p style="margin:0 0 8px">Se registró un seguimiento con estado <b>RIESGO</b>.</p>
          <ul style="margin:8px 0 12px;padding-left:18px">
            <li><b>Negocio:</b> {negocio.nombre} (ID {negocio.id})</li>
            <li><b>Propietario:</b> {negocio.propietario or '—'}</li>
            <li><b>Administrador:</b> {negocio.admin or '—'}</li>
            <li><b>Dirección:</b> {dir_text}</li>
            <li><b>Comercial(es):</b> {com_str}</li>
            <li><b>Fecha:</b> {fecha}</li>
            <li><b>Registrado por:</b> {uemail or '—'}</li>
          </ul>
          <p style="margin:0 0 6px"><b>Observación:</b></p>
          <pre style="white-space:pre-wrap;background:#f6f7fb;border-radius:8px;padding:10px;margin:6px 0;border:1px solid #e9ecef">{obs}</pre>
          <p style="margin:12px 0 0">Se adjunta un PDF con el detalle.</p>
        </div>
        """

        body_text = (
            "Alerta de riesgo\n"
            f"Negocio: {negocio.nombre} (ID {negocio.id})\n"
            f"Propietario: {negocio.propietario or '—'}\n"
            f"Administrador: {negocio.admin or '—'}\n"
            f"Dirección: {dir_text}\n"
            f"Comercial(es): {com_str}\n"
            f"Fecha: {fecha}\n"
            f"Registrado por: {uemail or '—'}\n\n"
            f"Observación:\n{obs}\n"
        )

        # Generar PDF (usa tu logo si existe en /static)
        logo_path = os.path.join(app.static_folder, 'logo2.png')
        pdf_buf = build_seguimiento_pdf(negocio, seg, logo_path=logo_path)

        # Enviar correo con adjunto
        send_mail_simple(
            list(destinatarios),
            asunto,
            body_html,
            body_text=body_text,
            attachments=[(f"Seguimiento_{negocio.id}.pdf", pdf_buf.getvalue(), "application/pdf")]
        )

    return jsonify(success=True, id=seg.id)


def ensure_tabla_seguimiento():
    db_path = 'negocios.db'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # ¿existe?
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seguimiento';")
    existe = cur.fetchone() is not None
    if not existe:
        cur.execute("""
        CREATE TABLE seguimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL,
            observacion TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'activo',
            creado_en DATETIME,
            creado_por_email TEXT,
            creado_por_id INTEGER,
            FOREIGN KEY(negocio_id) REFERENCES negocio(id)
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_negocio_id ON seguimiento(negocio_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_creado_por_email ON seguimiento(creado_por_email);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_creado_por_id ON seguimiento(creado_por_id);")
        conn.commit()
    conn.close()

# =======================
# Actualizar Cliente
# =======================
@app.route('/clientes/<int:cid>/actualizar', methods=['POST', 'PUT'])
def clientes_actualizar(cid):
    """
    Espera JSON como:
    {
      "nombre_negocio": "Texto",
      "estado_id": 2,                       # opcional
      "observaciones": "texto",             # opcional
      "contacto": {                         # opcional
          "nombre": "Juan",
          "telefono": "555-123"
      },
      "direcciones": [{                     # opcional (se toma la primera)
          "calle": "Av. 1",
          "municipio": "Centro",
          "provincia": "Pinar",
          "principal": 1
      }]
    }
    """
    data = request.get_json(silent=True) or {}

    cliente = Cliente.query.get_or_404(cid)

    # --- Campos simples ---
    nombre = (data.get('nombre_negocio') or '').strip()
    if nombre:
        cliente.nombre_negocio = nombre

    # estado (permitir None)
    estado_id = data.get('estado_id', None)
    if estado_id in ('', 'null'):
        estado_id = None
    try:
        cliente.estado_id = int(estado_id) if estado_id is not None else None
    except Exception:
        # si vino algo no convertible, lo ignoramos
        pass

    obs = (data.get('observaciones') or '').strip()
    cliente.observaciones = obs or None

    # --- Contacto (tomamos único principal/simple) ---
    contacto_payload = data.get('contacto') or {}
    if isinstance(contacto_payload, dict):
        c_nombre   = (contacto_payload.get('nombre') or '').strip() or None
        c_telefono = (contacto_payload.get('telefono') or '').strip() or None

        # buscamos contacto existente (el primero)
        contacto = (ClienteContacto.query
                    .filter_by(cliente_id=cliente.id)
                    .order_by(ClienteContacto.id.asc())
                    .first())
        if not contacto:
            # si no hay nada y llegó alguno de los campos, creamos
            if c_nombre or c_telefono:
                contacto = ClienteContacto(
                    cliente_id=cliente.id,
                    nombre=c_nombre,
                    telefono=c_telefono
                )
                db.session.add(contacto)
        else:
            # actualizar existente
            contacto.nombre = c_nombre
            contacto.telefono = c_telefono

    # --- Dirección principal (tomamos la primera del arreglo si viene) ---
    dirs_payload = data.get('direcciones') or []
    if isinstance(dirs_payload, list) and len(dirs_payload):
        d0 = dirs_payload[0] or {}
        d_calle     = (d0.get('calle') or '').strip() or None
        d_municipio = (d0.get('municipio') or '').strip() or None
        d_provincia = (d0.get('provincia') or '').strip() or None
        d_principal = 1 if d0.get('principal') in (True, 1, '1', 'true', 'True') else 0

        # buscamos dirección principal
        dir_principal = (ClienteDireccion.query
                         .filter_by(cliente_id=cliente.id, principal=True)
                         .first())
        if not dir_principal:
            # si no hay principal, tomamos la primera existente
            dir_principal = (ClienteDireccion.query
                             .filter_by(cliente_id=cliente.id)
                             .order_by(ClienteDireccion.id.asc())
                             .first())

        if not dir_principal:
            # crear si no existe ninguna y vino al menos una calle/municipio/provincia
            if d_calle or d_municipio or d_provincia:
                # si marcan esta como principal, desmarcamos otras
                if d_principal:
                    ClienteDireccion.query.filter_by(cliente_id=cliente.id, principal=True)\
                        .update({ClienteDireccion.principal: False})

                dir_principal = ClienteDireccion(
                    cliente_id=cliente.id,
                    calle=d_calle,
                    municipio=d_municipio,
                    provincia=d_provincia,
                    principal=bool(d_principal)
                )
                db.session.add(dir_principal)
        else:
            # actualizar campos
            dir_principal.calle     = d_calle
            dir_principal.municipio = d_municipio
            dir_principal.provincia = d_provincia

            # gestión de "principal"
            if d_principal:
                ClienteDireccion.query.filter_by(cliente_id=cliente.id, principal=True)\
                    .update({ClienteDireccion.principal: False})
                dir_principal.principal = True
            # si no viene como principal, mantenemos el estado actual

    try:
        db.session.commit()
        return jsonify(success=True, id=cliente.id)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e)), 400


# Crear cliente (AJAX desde el modal)
@app.route("/clientes/nuevo", methods=["POST"])
def clientes_nuevo():
    data = request.get_json(silent=True) or {}

    # ------- cliente -------
    nombre = (data.get("nombre_negocio") or "").strip()
    if not nombre:
        return jsonify(success=False, error="El nombre del cliente es obligatorio"), 400

    c = Cliente(
        nombre_negocio=nombre,
        estado_id=(int(data["estado_id"]) if data.get("estado_id") else None),
        observaciones=(data.get("observaciones") or "").strip() or None
    )
    db.session.add(c)
    db.session.flush()  # para obtener c.id

    # ------- contacto -------
    contacto = data.get("contacto") or {}
    if (contacto.get("nombre") or "").strip():
        db.session.add(ClienteContacto(
            cliente_id=c.id,
            nombre=(contacto.get("nombre") or "").strip(),
            telefono=(contacto.get("telefono") or "").strip() or None
        ))

    # ------- dirección -------
    dir_payloads = data.get("direcciones") or []
    if dir_payloads:
        d0 = dir_payloads[0] or {}
        calle      = (d0.get("calle") or "").strip()
        municipio  = (d0.get("municipio") or "").strip()
        provincia  = (d0.get("provincia") or "").strip()
        ciudad     = (d0.get("ciudad") or "").strip() or municipio  # 👈 usa municipio como ciudad
        principal  = bool(d0.get("principal"))

        # evitar NULL en campos NOT NULL
        if not calle:
            calle = "—"
        if not ciudad:
            ciudad = "—"
        if not municipio:
            municipio = "—"
        if not provincia:
            provincia = "—"

        db.session.add(ClienteDireccion(
            cliente_id=c.id,
            calle=calle,
            calle2=None,
            ciudad=ciudad,
            cp=None,
            pais=None,
            provincia=provincia,
            municipio=municipio,
            descripcion=None,
            principal=principal
        ))

    db.session.commit()
    return jsonify(success=True, id=c.id)




# ===== Eliminar cliente =====
@app.route('/clientes/<int:id>/eliminar', methods=['POST'])
def clientes_eliminar(id):
    c = Cliente.query.get_or_404(id)
    # borra dependencias (si no tienes cascade)
    try:
        ClienteContacto.query.filter_by(cliente_id=id).delete()
        ClienteDireccion.query.filter_by(cliente_id=id).delete()
    except Exception:
        pass

    db.session.delete(c)
    db.session.commit()
    flash('Cliente eliminado', 'success')
    return redirect(url_for('clientes_lista'))



@app.route('/clientes/<int:id>/json', methods=['GET'])
def cliente_json(id):
    c = Cliente.query.get_or_404(id)

    estado = None
    if getattr(c, 'estado', None):
        estado = {"id": c.estado.id, "nombre": c.estado.nombre}
    elif getattr(c, 'estado_id', None):
        est = ClienteEstado.query.get(c.estado_id)
        if est:
            estado = {"id": est.id, "nombre": est.nombre}

    contacto = None
    contactos = list(getattr(c, 'contactos', []))
    if contactos:
        pri = next((x for x in contactos if getattr(x, 'principal', False)), None)
        x = pri or contactos[0]
        contacto = {"id": x.id, "nombre": x.nombre, "telefono": x.telefono}

    direccion = None
    dirs = list(getattr(c, 'direcciones', []))
    if dirs:
        pri = next((d for d in dirs if getattr(d, 'principal', False)), None)
        d = pri or dirs[0]
        direccion = {
            "id": d.id,
            "calle": d.calle,
            "municipio": d.municipio,
            "provincia": d.provincia,
            "principal": bool(getattr(d, 'principal', False)),
        }

    return jsonify(success=True, cliente={
        "id": c.id,
        "nombre_negocio": getattr(c, 'nombre_negocio', None),
        "observaciones": getattr(c, 'observaciones', None),
        "creado_en": str(getattr(c, 'creado_en', '')),
        "estado": estado,
        "contacto": contacto,
        "direccion": direccion
    })


# -----------------------------------------------------------------------------
# Auth helpers
# -----------------------------------------------------------------------------


@app.route('/inicio')
@login_required
def inicio():
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = 12

    # base
    q_base = Negocio.query.order_by(Negocio.id.desc())

    # APLICA FILTRO POR ROL (incluye jefe de grupo)
    q = query_negocios_visibles_para_usuario(q_base)

    total = q.count()
    negocios = q.offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    total_paginas = (total + por_pagina - 1)//por_pagina

    # Comerciales para el modal (si lo usas en inicio)
    # Trae solo usuarios con rol 'comercial'
    rol_com = db_users.query(Role).filter_by(name='comercial').first()
    comerciales = db_users.query(User).filter(User.role == rol_com).order_by(User.nombre_completo, User.email).all() if rol_com else []

    return render_template(
        'inicio.html',
        negocios=negocios,
        pagina=pagina,
        total_paginas=total_paginas,
        comerciales=comerciales
    )
# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        user = db_users.query(User).filter_by(email=email).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['user_email'] = user.email
            session['user_role'] = user.role.name if user.role else None
            flash('Login exitoso.', 'success')
            return redirect(url_for('inicio'))
        flash('Credenciales inválidas.', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Has cerrado sesión.', 'success')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        if not email or not password:
            flash('Email y contraseña son obligatorios.', 'danger')
            return redirect(url_for('register'))
        if db_users.query(User).filter_by(email=email).first():
            flash('El correo ya está registrado.', 'danger')
            return redirect(url_for('register'))
        user_role = db_users.query(Role).filter_by(name='asistente').first()
        new_user = User(email=email, role=user_role)
        new_user.set_password(password)
        db_users.add(new_user)
        db_users.commit()
        flash('Registro exitoso. Por favor, inicia sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

# -----------------------------------------------------------------------------
# Panel/usuarios
# -----------------------------------------------------------------------------
@app.route('/editar_perfil', methods=['GET', 'POST'])
@login_required
def editar_perfil():
    email_actual = session.get('user_email')
    usuario = db_users.query(User).filter_by(email=email_actual).first()
    if not usuario:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('perfil'))
    if request.method == 'POST':
        nuevo_nombre = request.form.get('nombre_completo', '').strip()
        nueva_contrasena = request.form.get('nueva_contrasena', '').strip()
        if nuevo_nombre:
            usuario.nombre_completo = nuevo_nombre
        if nueva_contrasena:
            usuario.set_password(nueva_contrasena)
        db_users.commit()
        flash('Perfil actualizado correctamente.', 'success')
        return redirect(url_for('perfil'))
    return render_template('editar_perfil.html', usuario=usuario)

@app.route('/editar_usuario/<int:user_id>', methods=['GET', 'POST'])
@login_required
def editar_usuario(user_id):
    if session.get('user_role') != 'admin':
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('inicio'))
    usuario = db_users.query(User).get(user_id)
    roles = db_users.query(Role).all()
    if not usuario:
        flash('Usuario no encontrado.', 'warning')
        return redirect(url_for('panel_admin'))
    if request.method == 'POST':
        nombre = request.form.get('nombre_completo', '').strip()
        correo = request.form.get('email', '').strip().lower()
        nueva_contra = request.form.get('password', '').strip()
        nuevo_rol_id = request.form.get('rol')
        if nombre:
            usuario.nombre_completo = nombre
        if correo:
            usuario.email = correo
        if nueva_contra:
            usuario.set_password(nueva_contra)
        if nuevo_rol_id and int(nuevo_rol_id) != usuario.role_id:
            nuevo_rol = db_users.query(Role).get(int(nuevo_rol_id))
            if nuevo_rol:
                usuario.role = nuevo_rol
        db_users.commit()
        flash('Usuario actualizado correctamente.', 'success')
        return redirect(url_for('panel_admin'))
    return render_template('editar_usuario.html', usuario=usuario, roles=roles)

@app.route('/asignar_rol/<int:user_id>', methods=['POST'])
@login_required
def asignar_rol(user_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))
    nuevo_rol = request.form.get('rol')
    if nuevo_rol not in ['admin', 'comercial', 'asistente', 'usuario']:
        flash('Rol no válido.', 'danger')
        return redirect(url_for('panel_admin'))
    user = db_users.query(User).get(user_id)
    role_obj = db_users.query(Role).filter_by(name=nuevo_rol).first()
    if user and role_obj:
        user.role = role_obj
        db_users.commit()
        flash(f'Rol de {user.email} actualizado a {nuevo_rol}.', 'success')
    else:
        flash('Usuario o rol no encontrado.', 'warning')
    return redirect(url_for('panel_admin'))

@app.route('/panel_admin')
@login_required
def panel_admin():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))
    usuarios = db_users.query(User).all()
    return render_template('panel_admin.html', usuarios=usuarios)

@app.route('/crear_roles')
def crear_roles():
    crear_roles_iniciales()
    return "Roles iniciales creados (admin, comercial, asistente, usuario)."

# -----------------------------------------------------------------------------
# Negocios
# -----------------------------------------------------------------------------
@app.route("/")
def root():
    return redirect(url_for("inicio"))

@app.route('/buscar')
def buscar():
    qtext = (request.args.get('q') or '').strip().lower()

    # arranca de todo y aplica filtro por rol (incluye jefe)
    q = query_negocios_visibles_para_usuario(
        Negocio.query
    )

    if qtext:
        sub = db.session.query(ComercialNegocio.negocio_id).filter(
            or_(
                func.lower(ComercialNegocio.comercial_nombre).contains(qtext),
                func.lower(ComercialNegocio.comercial_email).contains(qtext)
            )
        ).subquery()
        q = q.filter(or_(
            func.lower(Negocio.nombre).contains(qtext),
            Negocio.id.in_(sub)
        ))

    negocios = q.all()
    return render_template('partials/_lista_negocios.html', negocios=negocios)


@app.route("/negocio/<int:id>/contacto", methods=["POST"])
def crear_contacto(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(silent=True) or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({"success": False, "error": "El nombre es obligatorio"}), 400

    c = Contacto(
        negocio_id=negocio.id,
        nombre=nombre,
        cargo=(data.get('cargo') or '').strip() or None,
        telefono=(data.get('telefono') or '').strip() or None,
        email=(data.get('email') or '').strip() or None,
        notas=(data.get('notas') or '').strip() or None,
        principal=bool(data.get('principal'))
    )

    if c.principal:
        Contacto.query.filter_by(negocio_id=negocio.id, principal=True) \
                      .update({Contacto.principal: False})

    db.session.add(c)
    db.session.commit()
    return jsonify({"success": True, "id": c.id})

@app.route('/admin/grupos', methods=['GET', 'POST'])
def admin_grupos():
    if session.get('user_role') != 'admin':
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('inicio'))

    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        if not nombre:
            flash('Nombre requerido', 'warning')
            return redirect(url_for('admin_grupos'))
        try:
            db_users.add(Grupo(nombre=nombre))
            db_users.commit()
            flash('Grupo creado.', 'success')
        except IntegrityError:
            db_users.rollback()
            flash('Ya existe un grupo con ese nombre.', 'warning')
        return redirect(url_for('admin_grupos'))

    grupos = db_users.query(Grupo).all()
    usuarios = db_users.query(User).order_by(User.nombre_completo, User.email).all()
    return render_template('admin/grupos.html', grupos=grupos, usuarios=usuarios)

@app.post('/admin/grupos/<int:gid>/add')
def admin_grupos_add(gid):
    if session.get('user_role') != 'admin':
        return redirect(url_for('inicio'))
    uid = int(request.form.get('user_id'))
    es_lider = 1 if request.form.get('es_lider') == '1' else 0

    g = db_users.query(Grupo).get(gid)
    u = db_users.query(User).get(uid)
    if not g or not u:
        flash('Grupo o usuario inexistente.', 'danger')
        return redirect(url_for('admin_grupos'))

    # Si marcamos líder, opcionalmente desmarcamos otros líderes del mismo grupo (1 líder)
    if es_lider:
        db_users.query(GrupoMiembro).filter_by(grupo_id=gid, es_lider=1).update({GrupoMiembro.es_lider: 0})

    gm = db_users.query(GrupoMiembro).filter_by(grupo_id=gid, user_id=uid).first()
    if gm:
        gm.es_lider = es_lider
    else:
        gm = GrupoMiembro(grupo_id=gid, user_id=uid, es_lider=es_lider)
        db_users.add(gm)
    db_users.commit()
    flash('Miembro agregado/actualizado.', 'success')
    return redirect(url_for('admin_grupos'))

@app.post('/admin/grupos/<int:gid>/del')
def admin_grupos_del(gid):
    if session.get('user_role') != 'admin':
        return redirect(url_for('inicio'))
    uid = int(request.form.get('user_id'))
    db_users.query(GrupoMiembro).filter_by(grupo_id=gid, user_id=uid).delete()
    db_users.commit()
    flash('Miembro eliminado.', 'success')
    return redirect(url_for('admin_grupos'))

@app.route("/contacto/<int:cid>", methods=["GET"])
def obtener_contacto(cid):
    c = Contacto.query.get_or_404(cid)
    return jsonify({
        "id": c.id,
        "negocio_id": c.negocio_id,
        "nombre": c.nombre,
        "cargo": c.cargo,
        "telefono": c.telefono,
        "email": c.email,
        "notas": c.notas,
        "principal": bool(c.principal),
    })

@app.route("/contacto/<int:cid>", methods=["PUT"])
def actualizar_contacto(cid):
    c = Contacto.query.get_or_404(cid)
    data = request.get_json(silent=True) or {}

    # actualizar campos
    for campo in ["nombre", "cargo", "telefono", "email", "notas"]:
        if campo in data:
            val = (data.get(campo) or "").strip()
            setattr(c, campo, val or None)

    # manejar principal
    if "principal" in data:
        nuevo_principal = bool(data.get("principal"))
        if nuevo_principal:
            Contacto.query.filter_by(negocio_id=c.negocio_id, principal=True) \
                          .update({Contacto.principal: False})
        c.principal = nuevo_principal

    db.session.commit()
    return jsonify({"success": True})

@app.route("/contacto/<int:cid>", methods=["DELETE"])
def eliminar_contacto(cid):
    c = Contacto.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/agregar", methods=["GET", "POST"])
def agregar_negocio():
    if request.method == "GET":
        casas_matriz = Negocio.query.filter_by(tipo='casa_matriz').order_by(Negocio.nombre).all()
        # comerciales desde usuarios.db con rol comercial
        rol_com = db_users.query(Role).filter_by(name='comercial').first()
        comerciales = db_users.query(User).filter(User.role == rol_com).order_by(User.nombre_completo, User.email).all() if rol_com else []
        return render_template("agregar.html", casas_matriz=casas_matriz, comerciales=comerciales)

    # POST: crear negocio
    nombre = request.form['nombre'].strip()
    propietario = request.form['propietario'].strip()
    admin = (request.form.get('admin') or '').strip()
    tel_propietario = (request.form.get('tel_propietario') or '').strip()
    tel_admin = (request.form.get('tel_admin') or '').strip()
    direccion = (request.form.get('direccion') or '').strip()
    licencia = (request.form.get('licencia') or '').strip()
    moneda_licencia = (request.form.get('moneda_licencia') or 'USD').strip()
    conectividad_raw = request.form.get('conectividad')
    conectividad = float(conectividad_raw) if conectividad_raw else 0.0
    tipo_negocio = request.form.get('tipo_negocio')

    if Negocio.query.filter_by(nombre=nombre).first():
        flash('Ya existe un negocio con ese nombre. Usa otro.', 'danger')
        return redirect(url_for('agregar_negocio'))

    padre_id = request.form.get('padre_id') if tipo_negocio == 'hijo' else None
    padre_id = int(padre_id) if padre_id else None
    tipo_final = 'casa_matriz' if tipo_negocio == 'negocio' else tipo_negocio

    nuevo = Negocio(
        nombre=nombre,
        propietario=propietario,
        admin=admin,
        tel_propietario=tel_propietario,
        tel_admin=tel_admin,
        direccion=direccion,
        licencia=licencia,
        moneda_licencia=moneda_licencia,
        conectividad=conectividad,
        tipo=tipo_final,
        tipo_negocio=tipo_final,
        padre_id=padre_id
    )
    db.session.add(nuevo)
    db.session.commit()

    # comerciales marcados (checkbox name="comercial[]", value=email)
    seleccion = request.form.getlist('comercial[]') or []
    for val in seleccion:
        val = (val or '').strip().lower()
        if not val:
            continue
        u = db_users.query(User).filter_by(email=val).first()
        nombre_corto = (u.nombre_completo or val.split("@")[0]) if u else (val.split("@")[0] if "@" in val else val)
        db.session.add(ComercialNegocio(
            negocio_id=nuevo.id,
            comercial_nombre=nombre_corto,
            comercial_email=(u.email if u else (val if "@" in val else None))
        ))
    db.session.commit()

    flash('Negocio agregado correctamente.', 'success')
    return redirect(url_for('inicio'))


# ---------- Datos para el modal ----------
# ---------- Datos para el modal ----------
@app.route('/detalle_negocio_modal/<int:id>')
def detalle_negocio_modal(id):
    negocio = Negocio.query.get_or_404(id)

    # Catálogos
    proveedores = ProveedorInternet.query.order_by(ProveedorInternet.nombre).all()
    modulos = Modulo.query.order_by(Modulo.nombre).all()

    # Módulos activos del negocio
    mods_activos = {
        nm.modulo_id
        for nm in NegocioModulo.query.filter_by(negocio_id=id, enabled=True).all()
    }

    # Comerciales (usuarios con rol "comercial"), por si la plantilla los necesita
    rol_com = db_users.query(Role).filter_by(name='comercial').first()
    comerciales_usuarios = (
        db_users.query(User).filter(User.role == rol_com).all() if rol_com else []
    )

    return render_template(
        'partials/detalle_negocio_modal.html',
        negocio=negocio,
        proveedores=proveedores,
        modulos=modulos,
        mods_activos=mods_activos,
        comerciales_usuarios=comerciales_usuarios
    )


# ---------- Asignar proveedor a un negocio ----------
@app.route('/negocio/<int:id>/set_proveedor', methods=['POST'])
def set_proveedor(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(silent=True) or request.form
    prov_id = data.get('proveedor_id')
    if not prov_id:
        negocio.proveedor_id = None
    else:
        p = ProveedorInternet.query.get(int(prov_id))
        if not p:
            return jsonify(success=False, error='Proveedor no existe'), 404
        negocio.proveedor_id = p.id
    db.session.commit()
    return jsonify(success=True)

# ---------- Activar/Desactivar un módulo para un negocio ----------
@app.route('/negocio/<int:id>/set_modulo', methods=['POST'])
def set_modulo(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(force=True)
    modulo_id = int(data.get('modulo_id'))
    enabled = bool(data.get('enabled'))
    modulo = Modulo.query.get_or_404(modulo_id)

    rel = NegocioModulo.query.filter_by(negocio_id=negocio.id, modulo_id=modulo.id).first()
    if not rel:
        rel = NegocioModulo(negocio_id=negocio.id, modulo_id=modulo.id, enabled=enabled)
        db.session.add(rel)
    else:
        rel.enabled = enabled
    db.session.commit()
    return jsonify(success=True)

# ---------- Panel admin simple para catálogos ----------
@app.route('/admin/catalogos', methods=['GET'])
@login_required
def admin_catalogos():
    if session.get('user_role') != 'admin':
        flash('Acceso denegado.', 'danger'); return redirect(url_for('inicio'))
    proveedores = ProveedorInternet.query.order_by(ProveedorInternet.nombre).all()
    modulos = Modulo.query.order_by(Modulo.nombre).all()
    return render_template('admin/catalogos.html', proveedores=proveedores, modulos=modulos)

@app.route('/admin/proveedor', methods=['POST', 'DELETE'])
@login_required
def admin_proveedor():
    if session.get('user_role') != 'admin':
        return jsonify(success=False, error='Acceso denegado'), 403
    if request.method == 'POST':
      nombre = (request.form.get('nombre') or '').strip()
      desc = (request.form.get('descripcion') or '').strip() or None
      if not nombre: return jsonify(success=False, error='Nombre requerido'), 400
      if ProveedorInternet.query.filter_by(nombre=nombre).first():
          return jsonify(success=False, error='Ya existe'), 400
      db.session.add(ProveedorInternet(nombre=nombre, descripcion=desc))
      db.session.commit(); return jsonify(success=True)
    # DELETE
    pid = int(request.args.get('id'))
    p = ProveedorInternet.query.get_or_404(pid)
    # limpiar referencias
    Negocio.query.filter_by(proveedor_id=pid).update({Negocio.proveedor_id: None})
    db.session.delete(p); db.session.commit()
    return jsonify(success=True)

@app.route('/admin/modulo', methods=['POST', 'DELETE'])
@login_required
def admin_modulo():
    if session.get('user_role') != 'admin':
        return jsonify(success=False, error='Acceso denegado'), 403
    if request.method == 'POST':
      nombre = (request.form.get('nombre') or '').strip()
      desc = (request.form.get('descripcion') or '').strip() or 'Sin descripción'
      if not nombre: return jsonify(success=False, error='Nombre requerido'), 400
      if Modulo.query.filter_by(nombre=nombre).first():
          return jsonify(success=False, error='Ya existe'), 400
      db.session.add(Modulo(nombre=nombre, descripcion=desc))
      db.session.commit(); return jsonify(success=True)
    # DELETE
    mid = int(request.args.get('id'))
    m = Modulo.query.get_or_404(mid)
    NegocioModulo.query.filter_by(modulo_id=mid).delete()
    db.session.delete(m); db.session.commit()
    return jsonify(success=True)


# -----------------------------------------------------------------------------
# Direcciones (endpoints usados por el modal)
# -----------------------------------------------------------------------------
@app.route("/negocio/<int:id>/direccion", methods=["POST"])
def agregar_direccion(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(silent=True) or request.form
    try:
        nueva_dir = Direccion(
            calle=(data.get("calle") or "").strip(),
            calle2=(data.get("calle2") or "").strip() or None,
            ciudad=(data.get("ciudad") or "").strip(),
            cp=(data.get("cp") or "").strip() or None,
            pais=(data.get("pais") or "").strip() or None,
            provincia=(data.get("provincia") or "").strip(),
            municipio=(data.get("municipio") or "").strip(),
            descripcion=(data.get("descripcion") or "").strip() or None,
            principal=bool(data.get("principal")),
            negocio_id=negocio.id,
        )
        if nueva_dir.principal:
            Direccion.query.filter_by(negocio_id=negocio.id, principal=True) \
                           .update({Direccion.principal: False})
        db.session.add(nueva_dir)
        db.session.commit()
        return jsonify({"success": True, "id": nueva_dir.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/direccion/<int:dir_id>", methods=["GET"])
def obtener_direccion(dir_id):
    d = Direccion.query.get_or_404(dir_id)
    return jsonify({
        "id": d.id,
        "calle": d.calle,
        "calle2": d.calle2,
        "ciudad": d.ciudad,
        "cp": d.cp,
        "pais": d.pais,
        "provincia": d.provincia,
        "municipio": d.municipio,
        "descripcion": d.descripcion,
        "principal": bool(d.principal),
        "negocio_id": d.negocio_id,
    })


def ensure_tabla_seguimiento():
    db_path = 'negocios.db'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # ¿existe?
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seguimiento';")
    existe = cur.fetchone() is not None
    if not existe:
        cur.execute("""
        CREATE TABLE seguimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL,
            observacion TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'activo',
            creado_en DATETIME,
            creado_por_email TEXT,
            creado_por_id INTEGER,
            FOREIGN KEY(negocio_id) REFERENCES negocio(id)
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_negocio_id ON seguimiento(negocio_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_creado_por_email ON seguimiento(creado_por_email);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_seguimiento_creado_por_id ON seguimiento(creado_por_id);")
        conn.commit()
    conn.close()


@app.route("/direccion/<int:dir_id>", methods=["PUT"])
def actualizar_direccion_simple(dir_id):
    d = Direccion.query.get_or_404(dir_id)
    data = request.get_json(silent=True) or {}

    for campo in ["calle","calle2","ciudad","cp","pais","provincia","municipio","descripcion"]:
        if campo in data:
            setattr(d, campo, (data.get(campo) or "").strip() or None)

    if "principal" in data:
        nuevo_principal = bool(data.get("principal"))
        if nuevo_principal:
            Direccion.query.filter_by(negocio_id=d.negocio_id, principal=True) \
                           .update({Direccion.principal: False})
        d.principal = nuevo_principal

    db.session.commit()
    return jsonify({"success": True})

@app.route("/direccion/<int:dir_id>", methods=["DELETE"])
def eliminar_direccion_simple(dir_id):
    d = Direccion.query.get_or_404(dir_id)
    db.session.delete(d)
    db.session.commit()
    return jsonify({"success": True})

    
@app.route('/eliminar_negocio/<int:negocio_id>', methods=['POST'])
def eliminar_negocio(negocio_id):
    negocio = Negocio.query.get_or_404(negocio_id)
    db.session.delete(negocio)
    db.session.commit()
    flash('Negocio eliminado correctamente.', 'success')
    return redirect(url_for('inicio'))

# -----------------------------------------------------------------------------
# Conectividad / Ediciones varias
# -----------------------------------------------------------------------------
@app.route('/conectividad', methods=['GET', 'POST'])
@login_required
def conectividad():
    negocios = Negocio.query.all()
    if request.method == 'POST':
        try:
            negocio_id = int(request.form.get('negocio_id'))
            conectividad_valor = float(request.form.get('conectividad', 0.0))
            negocio = Negocio.query.get_or_404(negocio_id)
            negocio.conectividad += conectividad_valor
            db.session.commit()
            flash(f'Conectividad actualizada para {negocio.nombre}. Total: {negocio.conectividad}', 'success')
        except Exception as e:
            flash('Error al actualizar conectividad: ' + str(e), 'danger')
        return redirect(url_for('conectividad'))
    return render_template('conectividad.html', negocios=negocios)

@app.route("/negocio/<int:id>/editar", methods=["POST"])
def editar_negocio(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(silent=True) or request.form

    def keep_if_blank(field, current):
        if field not in data: return current
        val = data.get(field)
        if val is None: return current
        val = str(val).strip()
        return current if val == "" else val

    negocio.nombre           = keep_if_blank("nombre", negocio.nombre)
    negocio.propietario      = keep_if_blank("propietario", negocio.propietario)
    negocio.admin            = keep_if_blank("admin", negocio.admin)
    negocio.direccion        = keep_if_blank("direccion", negocio.direccion)
    negocio.tel_propietario  = keep_if_blank("tel_propietario", negocio.tel_propietario)
    negocio.tel_admin        = keep_if_blank("tel_admin", negocio.tel_admin)
    negocio.observacion      = keep_if_blank("observacion", negocio.observacion)

    # 👇 añade estas dos líneas si no las tenías
    negocio.licencia         = keep_if_blank("licencia", negocio.licencia)
    negocio.moneda_licencia  = keep_if_blank("moneda_licencia", negocio.moneda_licencia)

    try:
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/negocio/<int:negocio_id>/comerciales", methods=["PUT"])
def actualizar_comerciales(negocio_id):
    negocio = Negocio.query.get_or_404(negocio_id)
    data = request.get_json(silent=True) or {}
    emails = data.get("emails", []) or []

    # borra relaciones actuales
    ComercialNegocio.query.filter_by(negocio_id=negocio.id).delete()

    # crea nuevas por email (y nombre, si existe usuario)
    etiqueta = []
    for email in emails:
        email = (email or "").strip().lower()
        if not email:
            continue
        u = db_users.query(User).filter_by(email=email).first()
        nombre = (u.nombre_completo if u and u.nombre_completo
                  else (email.split("@")[0] if email else None))
        db.session.add(ComercialNegocio(
            negocio_id=negocio.id,
            comercial_nombre=nombre,
            comercial_email=email
        ))
        etiqueta.append(nombre or email)

    db.session.commit()
    # Devuelve string listo para mostrar en el badge
    return jsonify({"success": True, "etiqueta": ", ".join(etiqueta) if etiqueta else "—"})

# -----------------------------------------------------------------------------
# Facturación
# -----------------------------------------------------------------------------
@app.route('/facturar_comercial')
@login_required
def facturar_comercial():
    usuario = db_users.query(User).get(session['user_id'])
    if not usuario:
        flash("Usuario no encontrado.", "danger")
        return redirect(url_for('inicio'))
    nombre_usuario = usuario.nombre_completo or usuario.email

    negocios = Negocio.query.join(ComercialNegocio) \
        .filter(or_(ComercialNegocio.comercial_email == usuario.email,
                    ComercialNegocio.comercial_nombre == nombre_usuario)) \
        .distinct().all()

    return render_template('facturacion/facturar_comercial.html',
                           negocios=negocios,
                           comercial=nombre_usuario)

@app.route('/liquidar_negocio', methods=['POST'])
def liquidar_negocio():
    negocio_id = request.form.get('negocio_id')
    metodo = request.form.get('metodo')
    monto = request.form.get('monto_total')

    negocio = Negocio.query.get(negocio_id)
    if not negocio:
        flash("Negocio no encontrado.", "danger")
        return redirect(url_for('facturar_comercial'))

    # armar string de comerciales
    com_str = comerciales_str(negocio.comerciales)

    log = f"🧾 Liquidación - Negocio: {negocio.nombre}, Comercial(es): {com_str}, Monto: {monto}, Método: {metodo}, Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    print(log)
    with open("logs_liquidaciones.txt", "a", encoding="utf-8") as f:
        f.write(log + "\n")

    flash(f"Negocio '{negocio.nombre}' liquidado exitosamente.", "success")
    return redirect(url_for('facturar_comercial'))

@app.route('/generar_todas_facturas')
def generar_todas_facturas():
    negocios = Negocio.query.filter(Negocio.tipo != 'hijo').all()
    template_path = os.path.join('facturas', 'plantilla_factura.pdf')
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        for negocio in negocios:
            try:
                conectividad_val = float(negocio.conectividad or 0)
            except ValueError:
                conectividad_val = 0
            try:
                licencia_value = float(negocio.licencia or 0)
            except ValueError:
                licencia_value = 0

            fecha_actual = datetime.now().strftime('%d/%m/%Y')
            numero = random.randint(1000, 9999)

            packet = BytesIO()
            can = canvas.Canvas(packet, pagesize=letter)
            can.setFont("Helvetica", 12)
            can.drawString(35, 555, f"{negocio.nombre}")

            # niveles
            if 50 <= licencia_value <= 90:
                nivel = "Nivel 1"
            elif 100 <= licencia_value <= 140:
                nivel = "Nivel 2"
            elif 150 <= licencia_value <= 250:
                nivel = "Nivel 3"
            elif licencia_value > 250:
                nivel = "Nivel VIP"
            else:
                nivel = "Sin nivel"

            can.drawString(25, 500, f"Plan profesional/mes - {nivel}")
            can.setFont("Helvetica", 10)
            can.drawString(400, 500, f"{negocio.licencia} USD")
            can.drawString(500, 500, f"{negocio.licencia} USD")
            can.drawString(525, 400, f"{negocio.licencia} USD")
            can.drawString(532, 440, f"{negocio.licencia} USD")

            if conectividad_val != 0:
                conectividad_str = f"{int(conectividad_val)}" if float(conectividad_val).is_integer() else f"{conectividad_val}"
                can.drawString(500, 480, f"{conectividad_str} CUP")
                can.drawString(400, 480, f"{conectividad_str} CUP")
                can.drawString(528, 390, f"{conectividad_str} CUP")
                can.drawString(533, 430, f"{conectividad_str} CUP")
                can.setFont("Helvetica", 12)
                can.drawString(25, 480, f"Conectividad")
                can.setFont("Helvetica", 10)
                can.drawString(293, 480, f"Unidad")
                can.drawString(351, 480, f"1")

            can.setFont("Helvetica", 9)
            can.drawString(490, 565, f"{fecha_actual}")
            can.drawString(489, 555, f": {numero}/2025")

            can.save()

            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            base_pdf = PdfReader(template_path)
            output_pdf = PdfWriter()

            page = base_pdf.pages[0]
            page.merge_page(overlay_pdf.pages[0])
            output_pdf.add_page(page)

            pdf_bytes = BytesIO()
            output_pdf.write(pdf_bytes)
            pdf_bytes.seek(0)

            zip_file.writestr(f"{negocio.nombre}_factura.pdf", pdf_bytes.read())

    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip',
                     download_name='facturas.zip', as_attachment=True)

@app.route("/clientes/<int:id>/informe")
def clientes_generar_informe(id):
    c = Cliente.query.get_or_404(id)

    # ---- Estado (string o relación) ----
    def get_estado(cliente):
        est = getattr(cliente, "estado", None)
        if hasattr(est, "nombre"):
            return est.nombre or "—"
        if isinstance(est, str):
            return est or "—"
        if getattr(cliente, "estado_id", None):
            cat = ClienteEstadoCatalogo.query.get(cliente.estado_id)
            if cat and getattr(cat, "nombre", None):
                return cat.nombre
        return "—"

    # ---- Contacto principal ----
    contacto = None
    if getattr(c, "contactos", None):
        contacto = next((x for x in c.contactos if getattr(x, "principal", False)), c.contactos[0])
    contacto_nombre = getattr(contacto, "", None) or ""
    contacto_tel    = getattr(contacto, "", None) or ""

    # ---- Dirección principal ----
    d = None
    if getattr(c, "direcciones", None):
        d = next((x for x in c.direcciones if getattr(x, "principal", False)), c.direcciones[0])
    dir_str = "—"
    if d:
        partes = [getattr(d, "calle", None), getattr(d, "municipio", None), getattr(d, "provincia", None)]
        dir_str = ", ".join([p for p in partes if p]) or "—"

    creado = c.creado_en.strftime("%d/%m/%Y %H:%M") if getattr(c, "creado_en", None) else "—"
    nombre = getattr(c, "nombre_negocio", None) or getattr(c, "nombre", None) or f"Cliente {c.id}"
    observ = (getattr(c, "observaciones", None) or "").strip()

    # ---------- Extraer "Sistema contratado" si viene en texto ----------
    sistema_contratado = "—"
    if observ:
        for raw in observ.splitlines():
            ln = raw.strip()
            low = ln.lower()
            if low.startswith("sistema contratado:") or low.startswith("sistema de contratacion:") or low.startswith("sistema de contratación:"):
                sistema_contratado = ln.split(":", 1)[1].strip() or "—"
                break

    # ---------- Clasificación de observaciones a 3 secciones ----------
    # Reglas simples por palabras clave (puedes añadir más si lo deseas)
    resumen, infraestructura, acuerdos = [], [], []

    KEY_INFRA = ("hardware", "router", "tablet", "dispositivo", "equipo",
                 "infraestructura", "conectividad", "software", "red", "internet")
    KEY_ACU   = ("acuerdo", "condicion", "condiciones", "aceptan", "responsable",
                 "pago", "precio", "cost", "monto", "economica", "económica")

    def bucketize(line):
        low = line.lower()
        if any(k in low for k in KEY_INFRA):
            infraestructura.append(line)
        elif any(k in low for k in KEY_ACU):
            acuerdos.append(line)
        else:
            resumen.append(line)

    if observ:
        for raw in observ.splitlines():
            ln = raw.strip()
            if not ln:
                continue
            # ignora la línea de "sistema contratado" para no repetir
            if ln.lower().startswith("sistema contratado:") or ln.lower().startswith("sistema de contratacion:") or ln.lower().startswith("sistema de contratación:"):
                continue
            # normaliza bullets del usuario
            if ln.startswith(("•","-","–","*")):
                ln = ln[1:].strip()
            bucketize(ln)

    # Si no hay nada, pon un marcador
    if not any([resumen, infraestructura, acuerdos]):
        resumen = ["—"]

    # ---------- Helpers de dibujo ----------
    PAGE_W, PAGE_H = letter
    LEFT, RIGHT = 50, PAGE_W - 50
    TOP, BOTTOM = PAGE_H - 50, 50
    LINE_H = 14

    def new_page(pdf):
        pdf.showPage()
        pdf.setFont("Helvetica", 11)

    def ensure_space(pdf, y, needed):
        if y - needed < BOTTOM:
            new_page(pdf)
            return TOP
        return y

    def wrap_text(text, max_chars=95):
        if not text:
            return ["—"]
        words = str(text).split()
        out, line = [], ""
        for w in words:
            if len(line) + len(w) + 1 <= max_chars:
                line = f"{line} {w}".strip()
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
        return out

    def draw_bullets(pdf, y, items, indent=12, bullet="•"):
        for it in items:
            lines = wrap_text(it, 95)
            for i, line in enumerate(lines):
                y = ensure_space(pdf, y, LINE_H)
                if i == 0:
                    pdf.drawString(LEFT + indent, y, f"{bullet} {line}")
                else:
                    pdf.drawString(LEFT + indent + 12, y, line)
                y -= LINE_H
        return y

    def draw_title(pdf, y, text):
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(LEFT, y, text)
        pdf.setFont("Helvetica", 11)
        return y - 28

    def draw_section_header(pdf, y, text):
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(LEFT, y, text)
        pdf.setFont("Helvetica", 11)
        return y - 16

    # ---------- PDF ----------
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    y = TOP

    # Título
    y = draw_title(pdf, y, f"Informe de Reunión - {nombre}")

    # Cabecera en 2 columnas (izq/der)
    estado = get_estado(c)
    pdf.drawString(LEFT, y, f"Estado: {estado}")
    pdf.drawRightString(RIGHT, y, f"Creado: {creado}")
    y -= 18

   

    pdf.drawString(LEFT, y, f"Dirección: {dir_str}")
    y -= 18

    pdf.drawString(LEFT, y, f"Sistema contratado: {sistema_contratado}")
    y -= 20

    # 1) Resumen del Cliente
    y = draw_section_header(pdf, y, "1. Resumen del Cliente")
    y = draw_bullets(pdf, y, resumen)
    y -= 4

    # 2) Equipamiento e Infraestructura
    y = draw_section_header(pdf, y, "2. Equipamiento e Infraestructura")
    y = draw_bullets(pdf, y, infraestructura or ["—"])
    y -= 4

    # 3) Acuerdos con el Cliente
    y = draw_section_header(pdf, y, "3. Acuerdos con el Cliente")
    y = draw_bullets(pdf, y, acuerdos or ["—"])

    pdf.showPage()
    pdf.save()
    buf.seek(0)

    safe_name = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "_" for ch in str(nombre)).strip().replace(" ", "_")
    return send_file(buf, as_attachment=True,
                     download_name=f"informe_cliente_{safe_name or c.id}.pdf",
                     mimetype="application/pdf")
@app.route('/generar_factura/<int:id>', methods=['POST'])
def generar_factura(id):
    negocio = Negocio.query.get_or_404(id)
    template_path = os.path.join('facturas', 'plantilla_factura.pdf')
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    can.setFont("Helvetica", 12)
    can.drawString(150, 700, f"Nombre: {negocio.nombre}")
    can.drawString(150, 680, f"Licencia: {negocio.licencia or '—'}")
    can.save()
    packet.seek(0)
    overlay_pdf = PdfReader(packet)
    base_pdf = PdfReader(template_path)
    output_pdf = PdfWriter()
    page = base_pdf.pages[0]
    page.merge_page(overlay_pdf.pages[0])
    output_pdf.add_page(page)
    pdf_bytes = BytesIO()
    output_pdf.write(pdf_bytes)
    pdf_bytes.seek(0)
    return send_file(pdf_bytes, mimetype='application/pdf',
                     download_name=f"{negocio.nombre}_factura.pdf", as_attachment=True)

# -----------------------------------------------------------------------------
# Perfil
# -----------------------------------------------------------------------------
@app.route('/perfil', methods=['GET'])
@login_required
def perfil():
    uid = session.get('user_id')
    if not uid:
        flash('Tu sesión expiró. Inicia sesión nuevamente.', 'warning')
        return redirect(url_for('login'))

    usuario = db_users.query(User).get(uid)
    if not usuario:
        session.clear()
        flash('No se encontró tu usuario. Inicia sesión nuevamente.', 'warning')
        return redirect(url_for('login'))

    logs = []
    log_file_path = 'log.txt'
    if os.path.exists(log_file_path):
        with open(log_file_path, 'r', encoding='utf-8') as f:
            logs = f.readlines()

    return render_template('perfil.html', usuario=usuario, logs=logs)


# -----------------------------------------------------------------------------
# APIs light
# -----------------------------------------------------------------------------
@app.route('/negocio/<int:id>/editar_campos', methods=['POST'])
def editar_campos(id):
    negocio = Negocio.query.get_or_404(id)
    data = request.get_json(force=True) or {}
    for campo in ['nombre', 'propietario', 'admin', 'direccion', 'observacion']:
        if campo in data and data[campo] is not None:
            setattr(negocio, campo, str(data[campo]).strip())
    db.session.commit()
    return jsonify(success=True)

@app.route('/api/agregar_negocio', methods=['POST'])
def api_agregar_negocio():
    if not request.is_json:
        return jsonify({"error": "Debe enviar datos en formato JSON"}), 400
    data = request.get_json()
    nombre = (data.get('nombre') or '').strip()
    propietario = (data.get('propietario') or '').strip()
    if not nombre or not propietario:
        return jsonify({"error": "Faltan campos obligatorios"}), 400
    if Negocio.query.filter_by(nombre=nombre).first():
        return jsonify({"error": "Nombre ya existe"}), 400
    nuevo = Negocio(
        nombre=nombre,
        propietario=propietario,
        admin=data.get('admin', ''),
        tel_propietario=data.get('tel_propietario', ''),
        tel_admin=data.get('tel_admin', ''),
        direccion=data.get('direccion', ''),
        licencia=data.get('licencia', ''),
        negocios_hijos=json.dumps(data.get('negocios_hijos', []))
    )
    db.session.add(nuevo)
    db.session.commit()
    return jsonify({"mensaje": "Negocio creado", "id": nuevo.id}), 201





# -----------------------------------------------------------------------------
# Modal detalle
# -----------------------------------------------------------------------------

@app.get("/api/dashboard")
@login_required
def api_dashboard():
    """
    Devuelve métricas y series para el dashboard en base a tus tablas:
      - Ingreso = suma de Negocio.licencia (numérico) para negocios con tipo != 'hijo'
      - Entrados = clientes creados (Cliente.creado_en) en ventana de 12 semanas
      - Bajas    = clientes con estado 'Cerrado' (si usas otra palabra, cámbiala)
    Soporta filtro por comercial (email) si llega ?com=correo
    """
    comercial_email = (request.args.get("com") or "").strip().lower()

    # ---- Filtro de negocios por comercial (si aplica) ----
    q_neg = Negocio.query.filter(Negocio.tipo != 'hijo')
    if comercial_email:
        q_neg = (q_neg.join(ComercialNegocio)
                      .filter(ComercialNegocio.comercial_email == comercial_email))

    # ---- Ingreso total (licencia) ----
    # Conviértelo a float con manejo de NULL/strings
    ingreso_total = 0.0
    for n in q_neg.all():
        try:
            ingreso_total += float(n.licencia or 0)
        except Exception:
            pass

    # ---- Ventana temporal (12 semanas) para series de clientes ----
    hoy = datetime.utcnow().date()
    hace_12_sem = hoy - timedelta(weeks=12)

    q_cli = Cliente.query.filter(Cliente.creado_en >= hace_12_sem)
    # si quieres filtrar por comercial, asume relación por email creador (creado_por_email)
    if comercial_email:
        q_cli = q_cli.filter(func.lower(Cliente.creado_por_email) == comercial_email)

    # Entrados: total clientes en ventana
    clientes_entrados = q_cli.count()

    # Bajas: clientes con estado "Cerrado" en ventana
    bajas_clientes = q_cli.filter(func.lower(Cliente.estado) == "cerrado").count()

    # Tasa de cierre aproximada
    tasa_cierre = 0.0
    denom = max(1, clientes_entrados)  # evita división por 0
    tasa_cierre = (bajas_clientes / denom) * 100.0

    # ---- Series por semana (W01..W52) usando SQLite strftime('%W') ----
    # Ingresos por semana = suma de licencia (estático, sin fecha). Para simular
    # una evolución, distribuimos “constante” o lo atamos a clientes creados.
    # Aquí usamos clientes por semana para la línea, y barras para entrados vs bajas.
    semana_label = func.strftime('%W', Cliente.creado_en)  # semana 00..53

    por_semana = (
        q_cli.with_entities(
            semana_label.label("wk"),
            func.count(Cliente.id).label("cnt"),
            func.sum(
                case(
                    (func.lower(Cliente.estado) == "cerrado", 1),
                    else_=0
                )
            ).label("cerr")
            )
        .group_by("wk")
        .order_by("wk")
        .all()
    )


    # Normaliza listas de 12 semanas (si faltan, pon 0)
    # Construimos un mapping wk->(entrados, bajas)
    wk_map = { row.wk: (row.cnt or 0, row.cerr or 0) for row in por_semana }

    # Etiquetas de las últimas 12 semanas (desde hace_12_sem a hoy)
    labels = []
    entrados = []
    bajas = []
    cur = hace_12_sem
    while cur <= hoy:
        wk = cur.strftime('%W')
        labels.append(f"W{wk}")
        e, b = wk_map.get(wk, (0, 0))
        entrados.append(int(e))
        bajas.append(int(b))
        cur += timedelta(weeks=1)

    # “Ingreso por semana” (línea) -> lo derivamos de entrados * ticket medio
    ticket_medio = 0.0
    try:
        total_clientes = sum(entrados) or 1
        ticket_medio = ingreso_total / total_clientes
    except Exception:
        ticket_medio = 0.0
    ingresos_line = [round(x * ticket_medio, 2) for x in entrados]

    # ---- Top 5 comerciales por “ingreso” (suma licencia de sus negocios) ----
    top_items = []
    rol_com = db_users.query(Role).filter_by(name='comercial').first()
    comerciales = db_users.query(User).filter(User.role == rol_com).all() if rol_com else []
    for u in comerciales:
        qn = (Negocio.query.filter(Negocio.tipo != 'hijo')
              .join(ComercialNegocio, isouter=True)
              .filter(ComercialNegocio.comercial_email == u.email))
        s = 0.0
        for n in qn.all():
            try:
                s += float(n.licencia or 0)
            except Exception:
                pass
        top_items.append({
            "nombre": u.nombre_completo or u.email.split("@")[0],
            "email": u.email,
            "ingreso": round(s, 2)
        })
    top_items.sort(key=lambda x: x["ingreso"], reverse=True)
    top5 = top_items[:5]

    # ---- Tabla comerciales por grupo ----
    # Grupo -> miembros -> ingreso sumado
    filas_grupo = []
    grupos = db_users.query(Grupo).all()
    for g in grupos:
        miembros = [gm.user for gm in g.miembros if gm.user and gm.user.email]
        for u in miembros:
            qn = (Negocio.query.filter(Negocio.tipo != 'hijo')
                  .join(ComercialNegocio, isouter=True)
                  .filter(ComercialNegocio.comercial_email == u.email))
            ingreso_u = 0.0
            for n in qn.all():
                try:
                    ingreso_u += float(n.licencia or 0)
                except Exception:
                    pass

            # Métricas “simples” de clientes del comercial
            qcu = Cliente.query
            qcu = qcu.filter(func.lower(Cliente.creado_por_email) == u.email.lower())
            entr = qcu.count()
            baj  = qcu.filter(func.lower(Cliente.estado) == "cerrado").count()
            visitados = entr + baj  # dummy
            cerrados = baj
            cierre = f"{(cerrados / max(1, visitados))*100:.1f}%"

            filas_grupo.append({
                "comercial": u.nombre_completo or u.email.split("@")[0],
                "grupo": g.nombre,
                "ingreso": round(ingreso_u, 2),
                "entrados": entr,
                "bajas": baj,
                "visitados": visitados,
                "cerrados": cerrados,
                "cierre": cierre
            })

    return jsonify({
        "ingreso_total": round(ingreso_total, 2),
        "clientes_entrados": clientes_entrados,
        "bajas_clientes": bajas_clientes,
        "tasa_cierre": round(tasa_cierre, 1),
        "series": {
            "labels": labels,
            "ingresos_por_semana": ingresos_line,
            "entrados": entrados,
            "bajas": bajas
        },
        "top5": top5,
        "comerciales_por_grupo": filas_grupo
    })






def migrate_sqlite():
    db_path = 'negocios.db'
    if not _sqlite_table_exists(db_path, 'negocio'):
        return
    if not _sqlite_column_exists(db_path, 'negocio', 'proveedor_id'):
        _sqlite_add_column(db_path, 'negocio', 'proveedor_id', 'INTEGER')



if __name__ == '__main__':
    with app.app_context():
        # Crea tablas en todas las DB (principal + binds)
        print("URI principal:", app.config['SQLALCHEMY_DATABASE_URI'])
        print("BINDS:", app.config.get('SQLALCHEMY_BINDS'))
        # seed_comerciales()

        db.create_all()      

    with app.app_context():
        db.create_all()
        for nombre in ['Posible cliente', 'Cerrado']:
            if not ClienteEstadoCatalogo.query.filter_by(nombre=nombre).first():
                db.session.add(ClienteEstadoCatalogo(nombre=nombre))
                db.session.commit()
        migrate_sqlite()
        migrate_clientes_autor()
        ensure_tabla_seguimiento()   # 👈 NUEVO


    app.run(debug=True)
