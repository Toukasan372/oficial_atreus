from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from datetime import datetime
import os
import zipfile
import json
import sqlite3
import random
from datetime import datetime
import json


from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base, relationship
from werkzeug.security import generate_password_hash, check_password_hash

# ----- Configuración Flask y bases -----
app = Flask(__name__)
app.config['SECRET_KEY'] = 'clave_muy_secreta'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///negocios.db'
db = SQLAlchemy(app)

# Base de datos secundaria para usuarios
user_db_path = 'usuarios.db'
engine_users = create_engine(f'sqlite:///{user_db_path}', connect_args={"check_same_thread": False})
db_users = scoped_session(sessionmaker(bind=engine_users))
BaseUsers = declarative_base()

# ⬇️ Aquí va el código para limpiar la base de datos de usuarios
BaseUsers.metadata.drop_all(bind=engine_users)
BaseUsers.metadata.create_all(bind=engine_users)
print("")

# 🔽 Agrega esto después de crear la app
@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return []

# ----- Funciones de utilidad para SQLite -----
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

# ----- Modelos para usuarios y roles -----
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

# ----- Función para crear roles iniciales -----
def crear_roles_iniciales():
    roles_necesarios = ['admin', 'comercial', 'asistente', 'usuario']
    for rol_nombre in roles_necesarios:
        rol_existente = db_users.query(Role).filter_by(name=rol_nombre).first()
        if not rol_existente:
            nuevo_rol = Role(name=rol_nombre)
            db_users.add(nuevo_rol)
    db_users.commit()

# ----- Crear base usuarios y tablas si no existen -----
if not os.path.exists(user_db_path):
    # Crear tablas
    BaseUsers.metadata.create_all(bind=engine_users)
    # Crear roles iniciales
    crear_roles_iniciales()
    # Crear admin inicial
    admin_role = db_users.query(Role).filter_by(name='admin').first()
    admin_user = User(email='admin@admin.com', role=admin_role)
    admin_user.set_password('admin')
    db_users.add(admin_user)
    db_users.commit()
else:
    # Si la tabla users existe, pero hay que verificar columnas
    if not tabla_existe(user_db_path, 'users'):
        BaseUsers.metadata.create_all(bind=engine_users)
        crear_roles_iniciales()
    # Verificar columna nombre_completo y agregar si no existe
    if not columna_existe(user_db_path, 'users', 'nombre_completo'):
        conn = sqlite3.connect(user_db_path)
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE users ADD COLUMN nombre_completo TEXT;")
        conn.commit()
        conn.close()
        print("Columna 'nombre_completo' agregada correctamente.")
    else:
        print("")

# ----- Modelo para negocios -----
class Negocio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    tipo_negocio = db.Column(db.String(20), default='negocio')  # Puede ser: negocio, casa_matriz, hijo
    tipo = db.Column(db.String(50), default='negocio')  # <- Este campo es obligatorio
    padre_id = db.Column(db.Integer, db.ForeignKey('negocio.id'), nullable=True)  # Referencia a casa matriz
    hijos = db.relationship('Negocio', backref=db.backref('padre', remote_side=[id]), lazy=True)
    telefonos_extras = db.Column(db.Text, nullable=True)  # para guardar múltiples teléfonos como texto
    # Guardaremos un JSON como: [{"nombre": "Juan", "telefono": "1234"}, ...]
    propietario = db.Column(db.String(100), nullable=False)
    admin = db.Column(db.String(100), nullable=True)
    tel_propietario = db.Column(db.String(20))
    tel_admin = db.Column(db.String(20), nullable=True)
    direccion = db.Column(db.String(200))
    negocios_hijos = db.Column(db.String(300))
    comercial = db.Column(db.String(100))
    observacion = db.Column(db.Text, nullable=True)
    licencia = db.Column(db.String(20))  # Licencia como número
    moneda_licencia = db.Column(db.String(3), default='USD')  # 'USD' o 'CUP'
    conectividad = db.Column(db.Float, default=0.0)  # Nuevo campo de conectividad, por defecto es 0.0




# ----- Decoradores y control de acceso -----


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_email'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ----- Rutas de autenticación y usuarios -----
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        user = db_users.query(User).filter_by(email=email).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['user_email'] = user.email
            session['user_role'] = user.role.name
            flash('Login exitoso.', 'success')
            return redirect(url_for('inicio'))
        else:
            flash('Credenciales inválidas.', 'danger')

            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Has cerrado sesión.', 'success')
    flash('Bienvenido', 'info')
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

@app.route('/cambiar_contrasena', methods=['GET', 'POST'])
@login_required
def cambiar_contrasena():
    # Aquí puedes implementar la lógica para cambiar contraseña
    return render_template('cambiar_contrasena.html')

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
        cambios = []
        if nombre and nombre != usuario.nombre_completo:
            cambios.append(f"Nombre: {usuario.nombre_completo} → {nombre}")
            usuario.nombre_completo = nombre
        if correo and correo != usuario.email:
            cambios.append(f"Correo: {usuario.email} → {correo}")
            usuario.email = correo
        if nueva_contra:
            cambios.append("Contraseña: actualizada")
            usuario.set_password(nueva_contra)
        if nuevo_rol_id and int(nuevo_rol_id) != usuario.role_id:
            rol_antiguo = usuario.role.name if usuario.role else "Ninguno"
            nuevo_rol = db_users.query(Role).get(int(nuevo_rol_id))
            if nuevo_rol:
                cambios.append(f"Rol: {rol_antiguo} → {nuevo_rol.name}")
                usuario.role = nuevo_rol
        db_users.commit()
        if cambios:
            log_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {session.get('user_email')} editó a {usuario.email}: " + "; ".join(cambios)
            with open("logs_usuarios.txt", "a", encoding="utf-8") as f:
                f.write(log_msg + "\n")
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

# ----- Rutas de negocios -----
@app.route("/")
def inicio():
    pagina = request.args.get("pagina", default=1, type=int)
    por_pagina = 10  # cantidad de negocios por página

    total = Negocio.query.count()
    negocios = Negocio.query.offset((pagina - 1) * por_pagina).limit(por_pagina).all()

    total_paginas = (total + por_pagina - 1) // por_pagina

    return render_template("inicio.html",
                           negocios=negocios,
                           pagina=pagina,
                           total_paginas=total_paginas)



@app.route('/buscar')
def buscar():
    q = request.args.get('q', '').strip().lower()
    if q:
        negocios = Negocio.query.filter(
            db.or_(
                db.func.lower(Negocio.nombre).contains(q),
                db.func.lower(Negocio.comercial).contains(q)
            )
        ).all()
    else:
        negocios = Negocio.query.all()
    return render_template('partials/_lista_negocios.html', negocios=negocios)


@app.route('/negocio/<int:id>')
def detalle_negocio(id):
    negocio = Negocio.query.get_or_404(id)

    hijos = []
    if negocio.tipo == 'casa_matriz' and negocio.negocios_hijos:
        try:
            datos = json.loads(negocio.negocios_hijos)
            ids = [int(x) for x in datos if isinstance(x, int)]
            hijos = Negocio.query.filter(Negocio.id.in_(ids), Negocio.tipo == 'hijo').all()
        except Exception as e:
            print("Error al procesar hijos:", e)

    return render_template('detalle_negocio.html', negocio=negocio, hijos=hijos)



@app.route('/facturar_comercial')
@login_required
def facturar_comercial():
    # Obtener nombre completo del usuario (si no hay, usar el email)
    usuario = db_users.query(User).get(session['user_id'])
    nombre_usuario = usuario.nombre_completo if usuario.nombre_completo else usuario.email

    # Buscar negocios cuyo campo comercial coincida con el nombre del usuario
    negocios = Negocio.query.filter_by(comercial=nombre_usuario).all()

    return render_template('facturacion/facturar_comercial.html',
                           negocios=negocios,
                           comercial=nombre_usuario)



@app.route('/agregar', methods=['GET', 'POST'])
def agregar_negocio():
    if request.method == 'POST':
        nombre = request.form['nombre']
        propietario = request.form['propietario']
        admin = request.form.get('admin')
        tel_propietario = request.form.get('tel_propietario')
        tel_admin = request.form.get('tel_admin')
        direccion = request.form.get('direccion')
        comercial = request.form.get('comercial')
        licencia = request.form.get('licencia')
        moneda_licencia = request.form.get('moneda_licencia')
        conectividad_raw = request.form.get('conectividad')
        conectividad = float(conectividad_raw) if conectividad_raw else 0.0
        tipo_negocio = request.form.get('tipo_negocio')

        if Negocio.query.filter_by(nombre=nombre).first():
            flash('Ya existe un negocio con ese nombre. Usa otro.', 'danger')
            return redirect(url_for('agregar_negocio'))

        # Si es hijo, se obtiene el padre_id
        padre_id = request.form.get('padre_id') if tipo_negocio == 'hijo' else None
        padre_id = int(padre_id) if padre_id else None

        # Si es "negocio" se convierte automáticamente en casa_matriz
        tipo_final = 'casa_matriz' if tipo_negocio == 'negocio' else tipo_negocio

        nuevo = Negocio(
            nombre=nombre,
            propietario=propietario,
            admin=admin,
            tel_propietario=tel_propietario,
            tel_admin=tel_admin,
            direccion=direccion,
            comercial=comercial,
            licencia=licencia,
            moneda_licencia=moneda_licencia,
            conectividad=conectividad,
            tipo=tipo_final,
            tipo_negocio=tipo_final,
            padre_id=padre_id
        )
        db.session.add(nuevo)
        db.session.commit()
        flash('Negocio agregado correctamente.', 'success')
        return redirect(url_for('inicio'))

    # GET: obtener casas matriz para el selector
    casas_matriz = Negocio.query.filter_by(tipo='casa_matriz').all()
    return render_template('agregar.html', casas_matriz=casas_matriz)



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



@app.route('/editar/<int:id>', methods=['POST'])
def editar_negocio(id):
    if session.get('user_role') != 'admin':
        flash('No tienes permiso para editar negocios.', 'danger')
        return redirect(url_for('inicio'))

    negocio = Negocio.query.get_or_404(id)
    data = request.form

    negocio.nombre = (data.get('nombre') or negocio.nombre or '').strip()
    negocio.propietario = (data.get('propietario') or negocio.propietario or '').strip()
    negocio.admin = (data.get('admin') or negocio.admin or '').strip()
    negocio.tel_propietario = (data.get('tel_propietario') or negocio.tel_propietario or '').strip()
    negocio.tel_admin = (data.get('tel_admin') or negocio.tel_admin or '').strip()
    negocio.direccion = (data.get('direccion') or negocio.direccion or '').strip()
    negocio.comercial = (data.get('comercial') or negocio.comercial or '').strip()
    negocio.observacion = data.get('observacion') or negocio.observacion
    negocio.licencia = (data.get('licencia') or negocio.licencia or '').strip()
    negocio.conectividad = float(data.get('conectividad') or negocio.conectividad or 0)

    # ✅ Negocios hijos
    negocios_hijos = (data.get('negocios_hijos') or negocio.negocios_hijos or '').strip()
    try:
        if negocios_hijos:
            json.loads(negocios_hijos)  # Validar que sea JSON válido
            negocio.negocios_hijos = negocios_hijos
    except Exception:
        flash('Los datos de negocios hijos están mal formateados.', 'danger')
        return redirect(url_for('detalle_negocio', id=id))

    telefonos_extras_raw = data.get('telefonos_extras')
    if telefonos_extras_raw:
        try:
            telefonos_json = json.loads(telefonos_extras_raw)
            if isinstance(telefonos_json, list):
             negocio.telefonos_extras = json.dumps(telefonos_json)
        except Exception:
            flash('Error al guardar teléfonos adicionales. Verifica el formato.', 'danger')
            return redirect(url_for('detalle_negocio', id=id))
    else:
        negocio.telefonos_extras = None

        print("Teléfonos guardados:", negocio.telefonos_extras)

    print("👉 Teléfonos a guardar:", data.get('telefonos_extras'))
    db.session.commit()
    flash('Negocio actualizado correctamente.')
    return redirect(url_for('detalle_negocio', id=id))



@app.route('/eliminar/<int:id>', methods=['POST'])
def eliminar_negocio(id):
    if session.get('user_role') != 'admin':
        flash('No tienes permiso para eliminar negocios.', 'danger')
        return redirect(url_for('inicio'))
    negocio = Negocio.query.get_or_404(id)
    db.session.delete(negocio)
    db.session.commit()
    flash('Negocio eliminado correctamente.')
    return redirect(url_for('inicio'))

# ----- Generación de facturas -----
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
                licencia_value = float(negocio.licencia)
            except ValueError:
                licencia_value = 0

            a = 5 + licencia_value
            fecha_actual = datetime.now().strftime('%d/%m/%Y')
            numero = random.randint(1000, 9999)

            packet = BytesIO()
            can = canvas.Canvas(packet, pagesize=letter)
            can.setFont("Helvetica", 12)
            can.drawString(35, 555, f"{negocio.nombre}")
            # Definir nivel según licencia_value
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
            can.drawString(400, 500, f"{negocio.licencia} USD" )
            can.drawString(500, 500, f"{negocio.licencia} USD" )
            can.drawString(525, 400, f"{negocio.licencia} USD" )
            can.drawString(532, 440, f"{negocio.licencia} USD" )

            # Mostrar conectividad solo si es distinta de 0
            if conectividad_val != 0:
                conectividad_str = f"{int(conectividad_val)}" if conectividad_val.is_integer() else f"{conectividad_val}"
                can.drawString(500, 480, f"{conectividad_str} CUP")
                can.drawString(400, 480, f"{conectividad_str} CUP")
                can.drawString(528, 390, f"{conectividad_str} CUP")
                can.drawString(533, 430, f"{conectividad_str} CUP")
                can.setFont("Helvetica", 12)
                can.drawString(25, 480, f"Conectividad" )
                can.setFont("Helvetica", 10)
                can.drawString(293, 480, f"Unidad" )
                can.drawString(351, 480, f"1" )


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

@app.route('/liquidar_negocio', methods=['POST'])
def liquidar_negocio():
    negocio_id = request.form.get('negocio_id')
    metodo = request.form.get('metodo')
    monto = request.form.get('monto_total')

    negocio = Negocio.query.get(negocio_id)
    if not negocio:
        flash("Negocio no encontrado.", "danger")
        return redirect(url_for('facturar_comercial'))

    # Aquí puedes guardar la liquidación en una tabla o simplemente imprimir en consola
    log = f"🧾 Liquidación - Negocio: {negocio.nombre}, Comercial: {negocio.comercial}, Monto: {monto}, Método: {metodo}, Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    print(log)

    # (Opcional) Guardar log en archivo
    with open("logs_liquidaciones.txt", "a", encoding="utf-8") as f:
        f.write(log + "\n")

    flash(f"Negocio '{negocio.nombre}' liquidado exitosamente.", "success")
    return redirect(url_for('facturar_comercial'))




@app.route('/generar_factura/<int:id>', methods=['POST'])
def generar_factura(id):
    negocio = Negocio.query.get_or_404(id)
    template_path = os.path.join('facturas', 'plantilla_factura.pdf')
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    can.setFont("Helvetica", 12)
    can.drawString(150, 700, f"Nombre: {negocio.nombre}")  # Ajusta las coordenadas (150, 700) 
    # Posicionar la licencia en el cuadro negro
    can.drawString(150, 680, f"Licencia: {negocio.licencia}")  # Ajusta las coordenadas (150, 680)
   
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
    render_template('inicio.html', usuario=usuario, logs=logs)
    return send_file(pdf_bytes, mimetype='application/pdf',
                     download_name=f"{negocio.nombre}_factura.pdf", as_attachment=True)

# ----- Otras rutas -----


@app.route('/perfil', methods=['GET'])
@login_required
def perfil():
    # Obtenemos el usuario actual
    usuario = db_users.query(User).get(session['user_id'])
    
    # Leemos el archivo de log
    logs = []
    log_file_path = 'log.txt'
    if os.path.exists(log_file_path):
        with open(log_file_path, 'r') as log_file:
            logs = log_file.readlines()

    # Mostrar la plantilla de perfil
    return render_template('perfil.html', usuario=usuario, logs=logs)

# ----- Apis -----
from flask import jsonify, request, session


@app.route('/api/agregar_negocio', methods=['POST'])
def api_agregar_negocio():
    if not request.is_json:
        return jsonify({"error": "Debe enviar datos en formato JSON"}), 400

    data = request.get_json()
    nombre = data.get('nombre', '').strip()
    propietario = data.get('propietario', '').strip()

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
        comercial=data.get('comercial', ''),
        licencia=data.get('licencia', ''),
        negocios_hijos=json.dumps(data.get('negocios_hijos', []))
    )
    db.session.add(nuevo)
    db.session.commit()

    return jsonify({"mensaje": "Negocio creado", "id": nuevo.id}), 201


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
