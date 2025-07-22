from flask import Blueprint, request, jsonify
from models import db, Negocio
import json

api = Blueprint('api', __name__)

@api.route('/api/agregar_negocio', methods=['POST'])
def agregar_negocio_api():
    data = request.json
    if not data:
        return jsonify({"error": "No se recibieron datos JSON"}), 400

    nombre = data.get('nombre', '').strip()
    propietario = data.get('propietario', '').strip()
    admin = data.get('admin', '').strip() if data.get('admin') else None
    tel_propietario = data.get('tel_propietario', '').strip() if data.get('tel_propietario') else None
    tel_admin = data.get('tel_admin', '').strip() if data.get('tel_admin') else None
    direccion = data.get('direccion', '').strip() if data.get('direccion') else None
    comercial = data.get('comercial', '').strip() if data.get('comercial') else None
    licencia = data.get('licencia', '').strip() if data.get('licencia') else None
    tipo = data.get('tipo', 'negocio').strip().lower()  # puede ser: negocio, casa_matriz, hijo
    id_padre = data.get('id_padre')  # solo si tipo == hijo
    negocios_hijos = data.get('negocios_hijos', [])
    telefonos_extras = json.dumps(data.get('telefonos_extras', []))  # lista de dicts

    # Validación
    if not nombre or not propietario:
        return jsonify({"error": "Los campos 'nombre' y 'propietario' son obligatorios"}), 400

    if Negocio.query.filter_by(nombre=nombre).first():
        return jsonify({"error": "Ya existe un negocio con ese nombre"}), 400

    # Ajustar tipo: un negocio "normal" se convierte en "casa matriz"
    if tipo == 'negocio':
        tipo = 'casa_matriz'

    if tipo == 'hijo':
        if not id_padre:
            return jsonify({"error": "Debe proporcionar 'id_padre' para un negocio hijo"}), 400
        padre = Negocio.query.get(id_padre)
        if not padre or padre.tipo != 'casa_matriz':
            return jsonify({"error": "El negocio padre no existe o no es una casa matriz"}), 400

    hijos_json = ''
    if tipo == 'casa_matriz' and isinstance(negocios_hijos, list):
        try:
            hijos_json = json.dumps(negocios_hijos)
        except Exception:
            hijos_json = ''

    nuevo = Negocio(
        nombre=nombre,
        propietario=propietario,
        admin=admin,
        tel_propietario=tel_propietario,
        tel_admin=tel_admin,
        direccion=direccion,
        comercial=comercial,
        licencia=licencia,
        tipo=tipo,
        id_padre=id_padre if tipo == 'hijo' else None,
        negocios_hijos=hijos_json if tipo == 'casa_matriz' else '',
        telefonos_extras=telefonos_extras
    )

    db.session.add(nuevo)
    db.session.commit()

    return jsonify({"mensaje": "Negocio agregado con éxito", "id": nuevo.id}), 201


@app.route('/api/agregar_conectividad', methods=['POST'])
def api_agregar_conectividad():
    data = request.get_json()
    negocio_id = data.get('negocio_id')
    conectividad = data.get('conectividad')
    
    negocio = Negocio.query.get_or_404(negocio_id)
    
    if not isinstance(conectividad, (int, float)):
        return jsonify({"error": "Conectividad debe ser un número válido"}), 400
    
    negocio.conectividad += conectividad
    db.session.commit()
    
    return jsonify({"message": "Conectividad actualizada", "total_conectividad": negocio.conectividad})
