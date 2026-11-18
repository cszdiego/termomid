"""
rutas_seguridad.py - Bitácora de seguridad y alertas SIEM.
Mínimo privilegio: SOLO el rol supervisor puede ver esto o liberar un sensor en cuarentena.
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import iso, obtener_db
from app.modelos import AlertaSiem, EventoSeguridad, Sensor
from app.seguridad import REGLAS, _ips_bloqueadas, solo_supervisor

router = APIRouter(prefix="/api", tags=["Seguridad (supervisor)"])


@router.get("/seguridad/eventos")
def eventos(limite: int = 50, db: Session = Depends(obtener_db), _u: dict = Depends(solo_supervisor)):
    """Últimos eventos de seguridad: hora, IP, sensor, endpoint, tipo, motivo y severidad."""
    filas = db.scalars(select(EventoSeguridad).order_by(EventoSeguridad.id.desc()).limit(min(limite, 500)))
    return [{"id": e.id, "fecha": iso(e.fecha), "ip": e.ip, "sensor_id": e.sensor_id, "endpoint": e.endpoint,
             "tipo": e.tipo, "motivo": e.motivo, "severidad": e.severidad} for e in filas]


@router.get("/seguridad/siem")
def alertas_siem(limite: int = 20, db: Session = Depends(obtener_db), _u: dict = Depends(solo_supervisor)):
    """Incidentes detectados por las reglas de correlación, más las IPs bloqueadas ahora mismo."""
    filas = db.scalars(select(AlertaSiem).order_by(AlertaSiem.id.desc()).limit(min(limite, 200)))
    ahora = time.time()
    return {
        "alertas": [{"id": a.id, "fecha": iso(a.fecha), "regla": a.regla, "severidad": a.severidad,
                     "ip": a.ip, "sensor_id": a.sensor_id, "descripcion": a.descripcion, "accion": a.accion}
                    for a in filas],
        "ips_bloqueadas": [{"ip": ip, "segundos_restantes": int(hasta - ahora)}
                           for ip, hasta in _ips_bloqueadas.items() if hasta > ahora],
    }


@router.get("/seguridad/reglas")
def reglas(_u: dict = Depends(solo_supervisor)):
    """Las reglas SIEM activas en formato condición / umbral / ventana / severidad / acción."""
    return [{**r, "tipos": sorted(r["tipos"])} for r in REGLAS]


@router.post("/sensores/{sensor_id}/liberar")
def liberar_sensor(sensor_id: str, db: Session = Depends(obtener_db), _u: dict = Depends(solo_supervisor)):
    """Saca un sensor de cuarentena (después de que la cuadrilla lo revisó)."""
    sensor = db.get(Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(404, "Sensor no encontrado")
    sensor.estado = "SIN_SENAL"  # vuelve a ACTIVO con su siguiente lectura válida
    db.commit()
    return {"id": sensor.id, "estado": sensor.estado}
