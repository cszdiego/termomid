"""
seguridad.py - Ciberseguridad de TermoMID.

1. Bitácora de eventos de seguridad (hora, IP, sensor, endpoint, motivo)
2. Reglas de correlación tipo SIEM (condición, umbral, ventana, severidad, acción)
3. Bloqueo temporal de IPs atacantes
4. Autenticación de sensores con llave única
5. Validación de rangos físicos y detección de lecturas manipuladas
6. Login de operadores con token firmado y roles (mínimo privilegio)
"""
import base64
import hashlib
import hmac
import time
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.database import ahora, obtener_db
from app.llaves import llave_sensor
from app.modelos import AlertaSiem, EventoSeguridad, Lectura, Sensor

# ================================================================ 1. BITÁCORA
def obtener_ip(request: Request) -> str:
    """IP real del cliente.

    - En local: la IP de la conexión.
    - En Render (CONFIAR_PROXY=true): se lee X-Forwarded-For, que es una lista "ip1, ip2, ...".
      El cliente puede escribir lo que quiera al PRINCIPIO de la lista (para fingir otra IP y
      evadir el bloqueo), pero el proxy de Render agrega la IP real al FINAL. Por eso se toma
      la entrada número PROXY_SALTOS contando desde la derecha, nunca la primera.
    """
    conexion = request.client.host if request.client else "desconocida"
    reenviada = request.headers.get("x-forwarded-for")
    if not (config.CONFIAR_PROXY and reenviada):
        return conexion
    partes = [p.strip() for p in reenviada.split(",") if p.strip()]
    if not partes:
        return conexion
    return partes[max(0, len(partes) - config.PROXY_SALTOS)][:64]


def registrar_evento(db: Session, *, ip: str, endpoint: str, tipo: str, motivo: str,
                     severidad: str, sensor_id: str | None = None) -> EventoSeguridad:
    """Guarda un evento de seguridad y evalúa las reglas SIEM.

    Los textos se recortan a su largo máximo: un atacante podría mandar un
    "sensor_id" gigante o con código HTML. Se guarda tal cual (es evidencia),
    y el dashboard lo muestra ESCAPADO para que no se ejecute (anti-XSS).
    """
    evento = EventoSeguridad(
        fecha=ahora(), ip=ip[:64], endpoint=endpoint[:100], tipo=tipo,
        motivo=motivo[:300], severidad=severidad,
        sensor_id=sensor_id[:64] if sensor_id else None,
    )
    db.add(evento)
    db.flush()  # que las reglas ya "vean" este evento al contar
    evaluar_reglas(db, evento)
    db.commit()
    print(f"[seguridad] {severidad} {tipo} ip={ip} sensor={sensor_id} :: {motivo}")
    return evento


# ================================================================ 2. REGLAS SIEM
# Cada regla está escrita como datos, igual que en un SIEM real:
# condición (qué eventos), umbral (cuántos), ventana (en cuánto tiempo), agrupado por, severidad y acción.
REGLA_INTRUSION = {
    "nombre": "R1 - Posible intrusión (fuerza bruta)",
    "tipos": {"LOGIN_FALLIDO", "LLAVE_INCORRECTA", "SENSOR_DESCONOCIDO", "TOKEN_INVALIDO"},
    "agrupar_por": "ip",
    "umbral": 5,
    "ventana_seg": 60,
    "severidad": "CRITICA",
    "accion": "Bloquear la IP por 5 minutos y notificar al responsable de seguridad",
}
REGLA_MANIPULACION = {
    "nombre": "R2 - Sensor manipulado",
    "tipos": {"LECTURA_MANIPULADA"},
    "agrupar_por": "sensor_id",
    "umbral": 3,
    "ventana_seg": 300,
    "severidad": "ALTA",
    "accion": "Poner el sensor en CUARENTENA (sus datos no se usan) y enviar cuadrilla a revisarlo",
}
REGLAS = [REGLA_INTRUSION, REGLA_MANIPULACION]

BLOQUEO_SEGUNDOS = 300
_ips_bloqueadas: dict[str, float] = {}  # ip -> momento (time.time) en que se desbloquea


def ip_bloqueada(ip: str) -> bool:
    return _ips_bloqueadas.get(ip, 0) > time.time()


def evaluar_reglas(db: Session, evento: EventoSeguridad) -> None:
    """Revisa si con este nuevo evento alguna regla supera su umbral."""
    for regla in REGLAS:
        if evento.tipo not in regla["tipos"]:
            continue
        campo = regla["agrupar_por"]           # "ip" o "sensor_id"
        valor = getattr(evento, campo)
        if not valor:
            continue
        columna = getattr(EventoSeguridad, campo)
        desde = ahora() - timedelta(seconds=regla["ventana_seg"])

        # Condición + umbral: ¿cuántos eventos de estos tipos hubo en la ventana?
        cuantos = db.scalar(select(func.count()).select_from(EventoSeguridad).where(
            columna == valor, EventoSeguridad.tipo.in_(regla["tipos"]), EventoSeguridad.fecha >= desde))
        if cuantos < regla["umbral"]:
            continue

        # Evita repetir la misma alerta SIEM dentro de la misma ventana
        columna_siem = getattr(AlertaSiem, campo)
        ya_existe = db.scalar(select(AlertaSiem.id).where(
            AlertaSiem.regla == regla["nombre"], columna_siem == valor, AlertaSiem.fecha >= desde).limit(1))
        if ya_existe:
            continue

        # Acción automática
        if regla is REGLA_INTRUSION:
            _ips_bloqueadas[valor] = time.time() + BLOQUEO_SEGUNDOS
        elif regla is REGLA_MANIPULACION:
            sensor = db.get(Sensor, valor)
            if sensor:
                sensor.estado = "CUARENTENA"

        db.add(AlertaSiem(
            fecha=ahora(), regla=regla["nombre"], severidad=regla["severidad"],
            ip=evento.ip if campo == "ip" else None,
            sensor_id=valor if campo == "sensor_id" else None,
            descripcion=f"{cuantos} eventos {sorted(regla['tipos'])} de {campo}={valor} "
                        f"en {regla['ventana_seg']} s (umbral {regla['umbral']})"[:300],
            accion=regla["accion"],
        ))
        print(f"[SIEM] *** {regla['severidad']} {regla['nombre']} ({campo}={valor}) -> {regla['accion']}")


# ================================================================ 3. SENSORES
def llave_correcta(sensor_id: str, llave: str | None) -> bool:
    """Compara la llave recibida con la esperada en tiempo constante (evita ataques de tiempo)."""
    if not llave:
        return False
    return hmac.compare_digest(llave_sensor(config.SENSOR_MASTER_KEY, sensor_id), llave)


# Rangos físicos posibles. Fuera de ellos el dato es un error o un ataque.
RANGOS = {
    "temperatura": (10.0, 60.0),
    "humedad": (0.0, 100.0),
    "globo_negro": (10.0, 80.0),
}


def error_de_rango(datos: dict) -> str | None:
    """Devuelve el motivo si algún valor está fuera de su rango físico; None si todo está bien."""
    for campo, (minimo, maximo) in RANGOS.items():
        valor = datos.get(campo)
        if valor is None or not (minimo <= valor <= maximo):
            return f"{campo}={valor} fuera del rango físico [{minimo}, {maximo}]"
    return None


SALTO_MAXIMO_C = 8.0        # °C: cambio imposible entre dos lecturas seguidas (están a segundos)
DESVIACION_MAXIMA_C = 6.0   # °C: diferencia máxima aceptable contra los otros sensores de la colonia


def detectar_manipulacion(db: Session, sensor: Sensor, temperatura: float) -> str | None:
    """Detecta un sensor manipulado (por ejemplo, alguien le acerca un encendedor o lo mete al sol).

    Regla A - salto imposible: la temperatura cambió más de 8 °C desde su lectura anterior.
    Regla B - desacuerdo con vecinos: difiere más de 6 °C de la mediana de los otros
              sensores de su colonia (si hay al menos 2 vecinos reportando).

    Ambas comparan solo contra lecturas RECIENTES (los últimos 3 ciclos, ~15 s). Ojo: con el
    día comprimido (1 día = 4 min), 2 minutos reales son 12 horas simuladas y la temperatura
    cambia mucho de forma natural; comparar contra una lectura tan vieja (por ejemplo, la de
    un sensor que se acaba de reconectar) daría falsos positivos.
    """
    reciente = ahora() - timedelta(seconds=max(15, 3 * config.INTERVALO_SEGUNDOS))
    anterior = db.scalar(select(Lectura.temperatura).where(
        Lectura.sensor_id == sensor.id, Lectura.sospechosa.is_(False), Lectura.fecha >= reciente,
    ).order_by(Lectura.id.desc()).limit(1))
    if anterior is not None and abs(temperatura - anterior) > SALTO_MAXIMO_C:
        return f"Salto imposible: {anterior:.1f} -> {temperatura:.1f} °C"

    vecinos = db.execute(select(Lectura.sensor_id, Lectura.temperatura).where(
        Lectura.colonia_id == sensor.colonia_id, Lectura.sensor_id != sensor.id,
        Lectura.sospechosa.is_(False), Lectura.fecha >= reciente,
    ).order_by(Lectura.id.desc())).all()
    ultima_por_vecino = {}
    for sid, temp in vecinos:
        ultima_por_vecino.setdefault(sid, temp)  # la primera que aparece es la más reciente
    if len(ultima_por_vecino) >= 2:
        valores = sorted(ultima_por_vecino.values())
        mediana = valores[len(valores) // 2]
        if abs(temperatura - mediana) > DESVIACION_MAXIMA_C:
            return f"Difiere {temperatura - mediana:+.1f} °C de la mediana de su colonia ({mediana:.1f} °C)"
    return None


# ================================================================ 4. OPERADORES (login y roles)
def _usuarios() -> dict:
    """Usuarios de demo. Las contraseñas vienen de variables de entorno, nunca del código."""
    return {
        "operador": {"rol": "operador", "password": config.OPERADOR_PASSWORD},
        "supervisor": {"rol": "supervisor", "password": config.SUPERVISOR_PASSWORD},
    }


def verificar_credenciales(usuario: str, password: str) -> dict | None:
    """Devuelve {"usuario", "rol"} si son correctas. Compara en tiempo constante.
    En producción: contraseñas guardadas con hash (Argon2/bcrypt) + MFA (código de app o llave física)."""
    datos = _usuarios().get(usuario)
    esperado = hashlib.sha256((datos["password"] if datos else "x").encode()).digest()
    recibido = hashlib.sha256(password.encode()).digest()
    if datos and hmac.compare_digest(esperado, recibido):
        return {"usuario": usuario, "rol": datos["rol"]}
    return None


DURACION_TOKEN_SEG = 8 * 3600  # un turno de 8 horas


def _firmar(texto: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), texto.encode(), hashlib.sha256).hexdigest()


def crear_token(usuario: str, rol: str) -> str:
    """Token = datos en base64 + "." + firma HMAC. Si alguien cambia el rol, la firma ya no coincide."""
    datos = f"{usuario}|{rol}|{int(time.time()) + DURACION_TOKEN_SEG}"
    return base64.urlsafe_b64encode(datos.encode()).decode() + "." + _firmar(datos)


def leer_token(token: str) -> dict | None:
    """Valida firma y caducidad. Devuelve {"usuario", "rol"} o None."""
    try:
        parte_datos, firma = token.split(".", 1)
        datos = base64.urlsafe_b64decode(parte_datos.encode()).decode()
        if not hmac.compare_digest(_firmar(datos), firma):
            return None
        usuario, rol, vence = datos.split("|")
        if int(vence) < time.time():
            return None
        return {"usuario": usuario, "rol": rol}
    except Exception:
        return None


_bearer = HTTPBearer(auto_error=False)  # agrega el botón "Authorize" en /docs


def usuario_actual(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                   db: Session = Depends(obtener_db)) -> dict:
    """Dependencia: exige un token válido (cualquier rol)."""
    if cred is None:
        raise HTTPException(401, "Falta iniciar sesión")
    usuario = leer_token(cred.credentials)
    if usuario is None:
        registrar_evento(db, ip=obtener_ip(request), endpoint=request.url.path, tipo="TOKEN_INVALIDO",
                         motivo="Token alterado o vencido", severidad="MEDIA")
        raise HTTPException(401, "Sesión inválida o vencida")
    return usuario


def solo_supervisor(request: Request, usuario: dict = Depends(usuario_actual),
                    db: Session = Depends(obtener_db)) -> dict:
    """Dependencia: mínimo privilegio. Solo el supervisor ve la bitácora de seguridad y libera sensores."""
    if usuario["rol"] != "supervisor":
        registrar_evento(db, ip=obtener_ip(request), endpoint=request.url.path, tipo="ACCESO_DENEGADO",
                         motivo=f"El usuario '{usuario['usuario']}' (rol {usuario['rol']}) intentó una acción de supervisor",
                         severidad="MEDIA")
        raise HTTPException(403, "Se requiere rol supervisor")
    return usuario


def verificar_demo(request: Request, db: Session) -> None:
    """Los controles de demostración piden la contraseña DEMO_PASSWORD en el encabezado X-Demo-Password."""
    recibida = request.headers.get("X-Demo-Password", "")
    if not hmac.compare_digest(recibida.encode(), config.DEMO_PASSWORD.encode()):
        registrar_evento(db, ip=obtener_ip(request), endpoint=request.url.path, tipo="LOGIN_FALLIDO",
                         motivo="Contraseña de demo incorrecta", severidad="MEDIA")
        raise HTTPException(401, "Contraseña de demo incorrecta")
