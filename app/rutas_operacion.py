"""
rutas_operacion.py - Endpoints del centro de mando de Protección Civil (requieren iniciar sesión).
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import alertas as flujo
from app import config
from app.calculos import distancia_km, peligro
from app.consultas import kpis, resumen_colonias
from app.database import ahora, iso, obtener_db
from app.mensajes import TIPOS_REPORTE, mensaje_alerta
from app.modelos import Alerta, Colonia, EstadoColonia, Refugio, ReporteCiudadano, Sensor
from app.pronostico import curva_colonia
from app.seguridad import (crear_token, ip_bloqueada, obtener_ip, registrar_evento,
                           usuario_actual, verificar_credenciales)

router = APIRouter(prefix="/api", tags=["Centro de mando"])


class LoginEntrada(BaseModel):
    usuario: str
    password: str


@router.post("/login")
def login(datos: LoginEntrada, request: Request, db: Session = Depends(obtener_db)):
    """Inicia sesión y devuelve un token. Cada fallo queda en la bitácora (y alimenta la regla SIEM R1).
    En producción aquí se pediría además un segundo factor (MFA)."""
    ip = obtener_ip(request)
    if ip_bloqueada(ip):
        raise HTTPException(429, "IP bloqueada temporalmente por la regla de intrusión")
    usuario = verificar_credenciales(datos.usuario, datos.password)
    if usuario is None:
        registrar_evento(db, ip=ip, endpoint="/api/login", tipo="LOGIN_FALLIDO",
                         motivo=f"Contraseña incorrecta para el usuario '{datos.usuario[:40]}'",
                         severidad="MEDIA")
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    return {"token": crear_token(usuario["usuario"], usuario["rol"]), **usuario}


@router.get("/yo")
def quien_soy(usuario: dict = Depends(usuario_actual)):
    return usuario


# ---------------------------------------------------------------- Conversión a JSON
def _sensor_json(s: Sensor) -> dict:
    return {"id": s.id, "colonia_id": s.colonia_id, "lat": s.lat, "lon": s.lon, "estado": s.estado,
            "ultima_lectura": iso(s.ultima_lectura)}


def _refugio_json(r: Refugio) -> dict:
    return {"id": r.id, "nombre": r.nombre, "colonia_id": r.colonia_id, "lat": r.lat, "lon": r.lon,
            "capacidad": r.capacidad, "ocupacion": r.ocupacion, "abierto": r.abierto}


def _alerta_json(db: Session, a: Alerta, colonias: dict[str, Colonia]) -> dict:
    """Alerta con todo lo que necesita la pantalla de aprobación: firmas, explicación y alcance."""
    c = colonias[a.colonia_id]
    firmas = flujo.firmas_del_nivel(a)
    requeridas = flujo.firmas_requeridas(a.nivel)
    refugio = None
    if a.refugio_id:
        refugio = db.get(Refugio, a.refugio_id)
    elif a.nivel in ("NARANJA", "ROJO"):
        refugio = flujo.refugio_mas_cercano(db, c)
    llamadas = a.nivel in ("NARANJA", "ROJO")
    return {
        "id": a.id, "colonia_id": a.colonia_id, "colonia": c.nombre,
        "nivel": a.nivel, "nivel_inicial": a.nivel_inicial, "indice_calor": a.indice_calor, "irc": a.irc,
        "estado": a.estado, "motivo": a.motivo,
        "creada": iso(a.creada), "actualizada": iso(a.actualizada), "cerrada_en": iso(a.cerrada_en),
        # --- Doble firma
        "firmas_requeridas": requeridas,
        "firmas": [{"usuario": ap.usuario, "rol": ap.rol, "fecha": iso(ap.fecha)} for ap in firmas],
        "firmas_faltantes": max(0, requeridas - len(firmas)) if a.estado in ("PROPUESTA", "SEGUNDA_FIRMA") else 0,
        "historial_firmas": [{"nivel": ap.nivel, "usuario": ap.usuario, "fecha": iso(ap.fecha)}
                             for ap in a.aprobaciones],
        # --- ¿Por qué lo propone el modelo? Los tres factores del IRC = P × V × (1 − C)
        "explicacion": {
            "peligro": peligro(a.indice_calor),
            "vulnerabilidad": c.vulnerabilidad,
            "falta_capacidad": round(1 - c.capacidad, 3),
            "pct_mayores": c.pct_mayores, "pct_sin_ac": c.pct_sin_ac, "arbolado": c.arbolado,
        },
        # --- Alcance estimado (o real, si ya se emitió)
        "alcance": {
            "poblacion": c.poblacion,
            "mayores_65": round(c.poblacion * c.pct_mayores),
            "sms": round(c.poblacion * flujo.ALCANCE_SMS),
            "whatsapp": round(c.poblacion * flujo.ALCANCE_WHATSAPP),
            "padron_llamadas": c.padron_mayores_solos if llamadas else 0,
            "refugio": None if refugio is None else {
                "id": refugio.id, "nombre": refugio.nombre, "abierto": refugio.abierto,
                "distancia_km": round(distancia_km(c.lat, c.lon, refugio.lat, refugio.lon), 1)},
        },
        "mensaje": mensaje_alerta(a.nivel, c.nombre, a.indice_calor),
        "emitida_en": iso(a.emitida_en), "emitida_por": a.emitida_por,
        "avisos_sms": a.avisos_sms, "avisos_whatsapp": a.avisos_whatsapp,
        "personas_a_llamar": a.personas_a_llamar, "refugio_id": a.refugio_id,
    }


def _alertas_recientes(db: Session, limite: int = 40, estado: str | None = None) -> list[dict]:
    """Primero las activas y luego las cerradas más recientes."""
    colonias = {c.id: c for c in db.scalars(select(Colonia))}
    consulta = select(Alerta).order_by(Alerta.id.desc()).limit(min(limite, 200))
    if estado:
        consulta = consulta.where(Alerta.estado == estado.upper())
    lista = [_alerta_json(db, a, colonias) for a in db.scalars(consulta)]
    lista.sort(key=lambda a: a["estado"] == "CERRADA")  # las activas arriba (orden estable)
    return lista


# ---------------------------------------------------------------- Tablero (todo en una sola llamada)
@router.get("/tablero")
def tablero(db: Session = Depends(obtener_db), usuario: dict = Depends(usuario_actual)):
    """Todo lo que el centro de mando necesita en UNA petición (se consulta cada 4 s):
    KPIs, colonias con pronóstico, alertas, refugios, sensores y reportes ciudadanos."""
    colonias = resumen_colonias(db)
    desde = ahora() - timedelta(hours=24)  # visibles hasta que alguien los atienda (máx. 24 h)
    nombres = {c["id"]: c["nombre"] for c in colonias}
    reportes = db.scalars(select(ReporteCiudadano).where(
        ReporteCiudadano.fecha >= desde, ReporteCiudadano.atendido.is_(False))
        .order_by(ReporteCiudadano.id.desc()).limit(20))
    return {
        "usuario": usuario,
        "kpis": kpis(db, colonias),
        "colonias": colonias,
        "alertas": _alertas_recientes(db),
        "refugios": [_refugio_json(r) for r in db.scalars(select(Refugio).order_by(Refugio.id))],
        "sensores": [_sensor_json(s) for s in db.scalars(select(Sensor).order_by(Sensor.id))],
        "reportes": [{"id": r.id, "fecha": iso(r.fecha), "colonia_id": r.colonia_id,
                      "colonia": nombres.get(r.colonia_id), "tipo": r.tipo,
                      "descripcion": TIPOS_REPORTE.get(r.tipo, r.tipo)} for r in reportes],
        "servidor": {"hora": iso(ahora()), "zona_horaria": config.ZONA_HORARIA},
    }


@router.get("/kpis")
def obtener_kpis(db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    """Indicadores del centro de mando."""
    return kpis(db)


@router.get("/colonias")
def colonias(db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    """Colonias ordenadas por riesgo (IRC), con estado actual y pronóstico +24/+48/+72 h."""
    return resumen_colonias(db)


@router.get("/colonias/{colonia_id}/historial")
def historial(colonia_id: str, minutos: int = 10, db: Session = Depends(obtener_db),
              _u: dict = Depends(usuario_actual)):
    """Serie medida del índice de calor de una colonia."""
    if db.get(Colonia, colonia_id) is None:
        raise HTTPException(404, "Colonia no encontrada")
    desde = ahora() - timedelta(minutes=min(minutos, 120))
    filas = db.scalars(select(EstadoColonia).where(
        EstadoColonia.colonia_id == colonia_id, EstadoColonia.fecha >= desde).order_by(EstadoColonia.id))
    return [{"fecha": iso(e.fecha), "hora_simulada": e.hora_simulada, "indice_calor": e.indice_calor,
             "indice_calor_ciudad": e.indice_calor_ciudad, "temperatura": e.temperatura,
             "humedad": e.humedad, "nivel": e.nivel} for e in filas]


@router.get("/colonias/{colonia_id}/curva")
def curva(colonia_id: str, db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    """Para la gráfica: lo MEDIDO en el último día simulado y el PRONÓSTICO a 72 h.
    El eje es en horas simuladas respecto a "ahora" (negativo = pasado)."""
    if db.get(Colonia, colonia_id) is None:
        raise HTTPException(404, "Colonia no encontrada")
    momento = ahora()
    desde = momento - timedelta(seconds=config.DIA_SEGUNDOS)
    filas = db.scalars(select(EstadoColonia).where(
        EstadoColonia.colonia_id == colonia_id, EstadoColonia.fecha >= desde).order_by(EstadoColonia.id))
    horas_por_segundo = 24 / config.DIA_SEGUNDOS
    medido = [{"h": round((e.fecha - momento).total_seconds() * horas_por_segundo, 2),
               "indice_calor": e.indice_calor} for e in filas]
    return {"medido": medido, "pronostico": curva_colonia(db, colonia_id)}


@router.get("/sensores")
def sensores(db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    return [_sensor_json(s) for s in db.scalars(select(Sensor).order_by(Sensor.id))]


@router.get("/refugios")
def refugios(db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    return [_refugio_json(r) for r in db.scalars(select(Refugio).order_by(Refugio.id))]


@router.post("/refugios/{refugio_id}/abrir")
def abrir_refugio(refugio_id: int, db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    refugio = db.get(Refugio, refugio_id)
    if refugio is None:
        raise HTTPException(404, "Refugio no encontrado")
    flujo.abrir_refugio(db, refugio)
    return _refugio_json(refugio)


@router.post("/reportes/{reporte_id}/atender")
def atender_reporte(reporte_id: int, db: Session = Depends(obtener_db), _u: dict = Depends(usuario_actual)):
    reporte = db.get(ReporteCiudadano, reporte_id)
    if reporte is None:
        raise HTTPException(404, "Reporte no encontrado")
    reporte.atendido = True
    db.commit()
    return {"id": reporte.id, "atendido": True}


@router.get("/alertas")
def listar_alertas(estado: str | None = None, limite: int = 50, db: Session = Depends(obtener_db),
                   _u: dict = Depends(usuario_actual)):
    """Alertas recientes (activas primero). Filtro opcional por estado."""
    return _alertas_recientes(db, limite, estado)


def _buscar_alerta(db: Session, alerta_id: int) -> Alerta:
    alerta = db.get(Alerta, alerta_id)
    if alerta is None:
        raise HTTPException(404, "Alerta no encontrada")
    return alerta


@router.post("/alertas/{alerta_id}/aprobar")
def aprobar(alerta_id: int, db: Session = Depends(obtener_db), usuario: dict = Depends(usuario_actual)):
    """Firma la alerta. La ROJA queda en SEGUNDA_FIRMA hasta que firme otro usuario."""
    alerta = flujo.aprobar(db, _buscar_alerta(db, alerta_id), usuario)
    return _alerta_json(db, alerta, {c.id: c for c in db.scalars(select(Colonia))})


@router.post("/alertas/{alerta_id}/emitir")
def emitir(alerta_id: int, db: Session = Depends(obtener_db), usuario: dict = Depends(usuario_actual)):
    """Emite el aviso (SMS/WhatsApp simulados), registra llamadas al padrón y abre refugio."""
    alerta = flujo.emitir(db, _buscar_alerta(db, alerta_id), usuario)
    return _alerta_json(db, alerta, {c.id: c for c in db.scalars(select(Colonia))})
