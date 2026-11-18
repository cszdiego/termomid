"""
rutas_publicas.py - Endpoints que NO requieren usuario de Protección Civil.

- /api/salud y /api/estado: para monitoreo y para el script externo.
- /api/lecturas: entrada de telemetría (se autentica con la LLAVE DEL SENSOR, no con usuario).
- /api/ciudadano/...: vista para la población (solo información a nivel colonia) y reportes anónimos.
"""
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.alertas import refugio_mas_cercano
from app.calculos import distancia_km
from app.consultas import ultimos_estados
from app.database import iso, obtener_db
from app.ingesta import recibir_lectura
from app.mensajes import CONSEJOS, RECOMENDACIONES, TIPOS_REPORTE
from app.modelos import Alerta, Colonia, ReporteCiudadano
from app.pronostico import curva_colonia, pronostico_colonias
from app.seguridad import obtener_ip, registrar_evento
from app.simulacion import OLA, reloj

router = APIRouter(prefix="/api", tags=["Público"])


@router.get("/salud")
def salud():
    """Prueba rápida de que el servidor está vivo."""
    return {"estado": "ok", "servicio": "TermoMID", "datos": "SIMULADOS"}


@router.get("/mi-ip")
def mi_ip(request: Request):
    """Diagnóstico: qué IP ve el servidor para TI (sirve para comprobar el proxy de Render).
    Solo devuelve datos del propio visitante."""
    return {"ip_detectada": obtener_ip(request),
            "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "ip_de_la_conexion": request.client.host if request.client else None,
            "confiar_proxy": config.CONFIAR_PROXY}


@router.get("/estado")
def estado():
    """Reloj simulado y si hay ola de calor (el script externo lo consulta)."""
    dia, hora = reloj(config.DIA_SEGUNDOS)
    return {"dia_simulado": dia, "hora_simulada": round(hora, 2), "ola_calor": OLA.extra(dia) > 0,
            "extra_ola_hoy": OLA.extra_actual(dia), "dia_segundos": config.DIA_SEGUNDOS,
            "simulador_interno": config.SIMULADOR_INTERNO}


class LecturaEntrada(BaseModel):
    """Lo que manda el gateway por cada sensor. Los rangos se validan en ingesta.py
    (así cada dato inválido queda registrado como evento de seguridad)."""
    sensor_id: str
    temperatura: float
    humedad: float
    globo_negro: float


@router.post("/lecturas", status_code=201)
def recibir(lectura: LecturaEntrada, request: Request, db: Session = Depends(obtener_db),
            x_sensor_key: str | None = Header(default=None, description="Llave única del sensor")):
    """Recibe una lectura del gateway LoRaWAN. Requiere el encabezado X-Sensor-Key."""
    return recibir_lectura(db, lectura.model_dump(), ip=obtener_ip(request),
                           endpoint="/api/lecturas", llave=x_sensor_key)


# ---------------------------------------------------------------- Vista ciudadana
@router.get("/ciudadano/colonias", tags=["Ciudadano"])
def lista_colonias(db: Session = Depends(obtener_db)):
    """Lista de colonias para el selector de la app ciudadana."""
    return [{"id": c.id, "nombre": c.nombre} for c in db.scalars(select(Colonia).order_by(Colonia.nombre))]


@router.get("/ciudadano/{colonia_id}", tags=["Ciudadano"])
def vista_ciudadano(colonia_id: str, db: Session = Depends(obtener_db)):
    """Riesgo actual de una colonia, recomendación (español y maya), pronóstico por hora,
    refugio cercano y último aviso EMITIDO por Protección Civil.
    Las alertas que aún no aprueba una persona NO se muestran al público."""
    colonia = db.get(Colonia, colonia_id)
    if colonia is None:
        raise HTTPException(404, "Colonia no encontrada")
    e = ultimos_estados(db).get(colonia_id)
    nivel = e.nivel if e else "VERDE"
    refugio = refugio_mas_cercano(db, colonia)
    aviso = db.scalars(select(Alerta).where(Alerta.colonia_id == colonia_id, Alerta.emitida_en.is_not(None))
                       .order_by(Alerta.emitida_en.desc()).limit(1)).first()
    pron = pronostico_colonias(db)[colonia_id]
    # Fichas por hora: las próximas 12 horas simuladas, cada 2 horas
    fichas = [p for p in curva_colonia(db, colonia_id, horas=12) if p["h"] % 2 == 0][1:]
    return {
        "colonia": colonia.nombre,
        "nivel": nivel,
        "indice_calor": e.indice_calor if e else None,
        "temperatura": e.temperatura if e else None,
        "recomendacion": RECOMENDACIONES[nivel],
        "consejos": CONSEJOS,
        "manana": pron["+24h"],
        "proximas_horas": fichas,
        "refugio_cercano": None if refugio is None else {
            "nombre": refugio.nombre, "abierto": refugio.abierto, "lat": refugio.lat, "lon": refugio.lon,
            "distancia_km": round(distancia_km(colonia.lat, colonia.lon, refugio.lat, refugio.lon), 1),
            "lugares_libres": refugio.capacidad - refugio.ocupacion,
        },
        # Solo se muestra el aviso si sigue vigente (la alerta no se ha cerrado)
        "ultimo_aviso": None if aviso is None or aviso.estado == "CERRADA" else {
            "id": aviso.id, "nivel": aviso.nivel, "emitido": iso(aviso.emitida_en),
            "indice_calor": aviso.indice_calor, "mensaje": RECOMENDACIONES[aviso.nivel],
        },
        "nota": "Datos SIMULADOS - prototipo académico",
    }


class ReporteEntrada(BaseModel):
    colonia_id: str
    tipo: str


# Límite anti-abuso: cada IP puede mandar 5 reportes cada 10 minutos.
# El contador vive en memoria y NO se guarda junto al reporte (el reporte es anónimo).
_reportes_por_ip: dict[str, list[float]] = {}
MAX_REPORTES, VENTANA_REPORTES = 5, 600


@router.post("/ciudadano/reportes", tags=["Ciudadano"], status_code=201)
def reportar(datos: ReporteEntrada, request: Request, db: Session = Depends(obtener_db)):
    """Reporte ANÓNIMO: no se pide nombre ni teléfono; solo se guarda la colonia y el tipo."""
    if datos.tipo not in TIPOS_REPORTE:
        raise HTTPException(422, f"Tipo inválido. Opciones: {list(TIPOS_REPORTE)}")
    if db.get(Colonia, datos.colonia_id) is None:
        raise HTTPException(404, "Colonia no encontrada")
    ip, ahora_s = obtener_ip(request), time.time()
    recientes = [t for t in _reportes_por_ip.get(ip, []) if t > ahora_s - VENTANA_REPORTES]
    if len(recientes) >= MAX_REPORTES:
        registrar_evento(db, ip=ip, endpoint="/api/ciudadano/reportes", tipo="ABUSO_REPORTES",
                         motivo=f"Más de {MAX_REPORTES} reportes en {VENTANA_REPORTES // 60} min desde la misma IP",
                         severidad="BAJA")
        raise HTTPException(429, "Demasiados reportes seguidos. Si es una emergencia, llama al 911.")
    _reportes_por_ip[ip] = recientes + [ahora_s]
    db.add(ReporteCiudadano(colonia_id=datos.colonia_id, tipo=datos.tipo))
    db.commit()
    return {"recibido": True, "mensaje": "Gracias. Protección Civil revisará tu reporte. Si es una emergencia, llama al 911."}
