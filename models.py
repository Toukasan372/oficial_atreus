from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
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
    licencia = db.Column(db.String(20))  # Licencia como número
    moneda_licencia = db.Column(db.String(3), default='USD')  # 'USD' o 'CUP'
    conectividad = db.Column(db.Float, default=0.0)  # Nuevo campo de conectividad, por defecto es 0.0
