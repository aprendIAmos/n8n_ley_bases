from flask import Flask, request, jsonify, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
import requests
import re
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# CORS para permitir requests desde tu frontend
#CORS(app, origins=["*"])  # Cambia "*" por tu dominio en producción
ALLOWED_ORIGINS = os.getenv("FRONTEND_URL", "*")
CORS(app, origins=[ALLOWED_ORIGINS])

# ==================== CONFIGURACIÓN ====================
N8N_WEBHOOK = os.getenv("N8N_WEBHOOK_URL")
MAX_MESSAGE_LENGTH = 500  # Máximo de caracteres por mensaje
REQUEST_TIMEOUT = 30  # Timeout para la request a n8n (segundos)

# ==================== RATE LIMITING ====================
limiter = Limiter(
    app=app,
    key_func=get_remote_address,  # Limita por IP
    default_limits=["100 per day", "20 per hour"],  # Límites globales
    storage_uri="memory://"  # Usa memoria (para producción usa Redis)
)

# ==================== FUNCIONES DE VALIDACIÓN ====================
def sanitize_input(text):
    """Limpia y valida la entrada del usuario"""
    if not text or not isinstance(text, str):
        return None
    
    # Elimina espacios excesivos
    text = text.strip()
    
    # Detecta patrones de inyección de prompt
    dangerous_patterns = [
        r"ignore\s+(previous|above|all)\s+instructions?",
        r"system\s*:?\s*(prompt|message|shutdown)",
        r"translate\s+the\s+above",
        r"forget\s+(everything|all|previous)",
        r"<\s*script",  # Intento de XSS
        r"DROP\s+TABLE",  # SQL injection básico
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return None
    
    return text

# ==================== RUTA RAÍZ (HOMEPAGE) ====================
@app.route("/", methods=["GET"])
def index():
    """Sirve el HTML principal"""
    return render_template("index.html")

# ==================== ARCHIVOS ESTÁTICOS ====================
# @app.route("/static/<path:filename>")
# def static_files(filename):
#     """Sirve archivos CSS, JS, imágenes, etc."""
#     return send_from_directory("static", filename)

# ==================== ENDPOINT PRINCIPAL ====================
@app.route("/chat", methods=["POST"])
@limiter.limit("3 per minute")  # Límite específico: 3 mensajes por minuto
def chat():
    try:
        data = request.json
        
        # Validación básica
        if not data:
            return jsonify({"error": "No se recibieron datos"}), 400
        
        user_msg = data.get("message")
        session_id = data.get("sessionId", "anonymous")
        
        # Validar que llegó un mensaje
        if not user_msg:
            return jsonify({"error": "El campo 'message' es requerido"}), 400
        
        # Sanitizar entrada
        clean_msg = sanitize_input(user_msg)
        if clean_msg is None:
            return jsonify({
                "error": "Mensaje inválido o contiene patrones prohibidos"
            }), 400
        
        # Validar longitud
        if len(clean_msg) > MAX_MESSAGE_LENGTH:
            return jsonify({
                "error": f"El mensaje excede el límite de {MAX_MESSAGE_LENGTH} caracteres"
            }), 400
        
        if len(clean_msg) < 3:
            return jsonify({
                "error": "El mensaje es demasiado corto"
            }), 400
        
        # ==================== LLAMADA A N8N ====================
        response = requests.post(
            N8N_WEBHOOK,
            json={
                "message": clean_msg,
                "sessionId": session_id
            },
            timeout=REQUEST_TIMEOUT,
            headers={"Content-Type": "application/json"}
        )
        
        # Verificar respuesta de n8n
        response.raise_for_status()
        # En lugar de return jsonify(response.json()), 200
        try:
            return jsonify(response.json()), 200
        except ValueError:
            # Si n8n devuelve texto plano en lugar de JSON
            return jsonify({"response": response.text}), 200
        #return jsonify(response.json()), 200
        
    except requests.exceptions.Timeout:
        return jsonify({
            "error": "El servicio tardó demasiado en responder. Intenta nuevamente."
        }), 504
        
    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": "Error al conectar con el servicio de consultas"
        }), 503
        
    except Exception as e:
        # No exponer detalles internos en producción
        app.logger.error(f"Error inesperado: {str(e)}")
        return jsonify({
            "error": "Ocurrió un error inesperado. Por favor intenta nuevamente."
        }), 500

# ==================== ENDPOINT DE SALUD ====================
@app.route("/health", methods=["GET"])
def health():
    """Endpoint para verificar que el servicio está activo"""
    return jsonify({"status": "ok"}), 200

# ==================== MANEJO DE RATE LIMIT ====================
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "Demasiadas solicitudes. Por favor espera un momento antes de intentar nuevamente."
    }), 429

# ==================== EJECUCIÓN ====================
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)  # debug=False para producción