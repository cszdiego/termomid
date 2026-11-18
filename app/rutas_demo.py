"""
rutas_demo.py - Controles para la exposición en vivo (protegidos con DEMO_PASSWORD).

Simulan situaciones para que las alertas aparezcan en vivo:
- Ola de calor            -> sube la temperatura 5 °C durante 3 días simulados.
- Tumbar un sensor        -> deja de transmitir 2 minutos.
- Sensor falso            -> lecturas de un sensor inexistente y de uno real con llave equivocada.
- Sensor manipulado       -> un sensor real manda 3 lecturas imposibles (58 °C) -> cuarentena.
- Ataque de fuerza bruta  -> 6 contraseñas incorrectas seguidas -> regla R1 -> IP bloqueada.

Los ataques usan la IP 203.0.113.66, que es una IP RESERVADA PARA DOCUMENTACIÓN (no existe en
Internet). Así el bloqueo cae sobre el "atacante" simulado y no sobre la computadora del expositor.
"""
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.alertas import cerrar_todas
from app.database import obtener_db
from app.ingesta import recibir_lectura
from app.llaves import llave_sensor
from app.modelos import Refugio, Sensor
from app.motor import GENERADOR
from app.seguridad import _ips_bloqueadas, registrar_evento, verificar_demo
from app.simulacion import OLA, reloj

router = APIRouter(prefix="/api/demo", tags=["Demo (requiere X-Demo-Password)"])

IP_ATACANTE = "203.0.113.66"       # el que prueba contraseñas
IP_SENSOR_FALSO = "203.0.113.77"   # el que se hace pasar por sensor


def control_demo(request: Request, db: Session = Depends(obtener_db)) -> None:
    verificar_demo(request, db)


class OlaEntrada(BaseModel):
    activar: bool = True
    dias: int = 3


@router.post("/ola-calor", dependencies=[Depends(control_demo)])
def ola_calor(datos: OlaEntrada):
    dia, _hora = reloj(config.DIA_SEGUNDOS)
    if datos.activar:
        # Sube en rampa durante 3 horas simuladas (1/8 de día)
        OLA.activar(dia, max(1, min(datos.dias, 5)), rampa_seg=config.DIA_SEGUNDOS / 8)
    else:
        OLA.desactivar()
    return {"ola_calor": datos.activar, "dia_inicio": OLA.dia_inicio, "dia_fin": OLA.dia_fin}


class SensorEntrada(BaseModel):
    sensor_id: str | None = None


@router.post("/tumbar-sensor", dependencies=[Depends(control_demo)])
def tumbar_sensor(datos: SensorEntrada):
    """Solo afecta al simulador interno (en la vida real: batería agotada o sin señal LoRa)."""
    sid = datos.sensor_id or random.choice(GENERADOR.sensores)["id"]
    GENERADOR.tumbar(sid, 120)
    return {"sensor_id": sid, "caido_por_segundos": 120}


def _intentar(db: Session, datos: dict, llave: str | None) -> str:
    try:
        r = recibir_lectura(db, datos, ip=IP_SENSOR_FALSO, endpoint="/api/lecturas", llave=llave)
        return "aceptada (sospechosa)" if r["sospechosa"] else "aceptada"
    except HTTPException as e:
        return f"rechazada {e.status_code}: {e.detail}"


@router.post("/sensor-falso", dependencies=[Depends(control_demo)])
def sensor_falso(db: Session = Depends(obtener_db)):
    """1) Un sensor que no existe (con HTML en el nombre, para probar que el dashboard lo escapa).
       2) Un sensor real, pero con una llave inventada (suplantación)."""
    lectura = {"temperatura": 35.0, "humedad": 50.0, "globo_negro": 40.0}
    falso = "FALSO-99<img src=x onerror=alert('XSS')>"
    return {
        "sensor_inexistente": _intentar(db, {**lectura, "sensor_id": falso}, "llave-inventada"),
        "llave_equivocada": _intentar(db, {**lectura, "sensor_id": "TMID-001"}, "0" * 64),
    }


@router.post("/sensor-manipulado", dependencies=[Depends(control_demo)])
def sensor_manipulado(datos: SensorEntrada, db: Session = Depends(obtener_db)):
    """Un sensor REAL (con su llave correcta) reporta 58 °C tres veces: alguien lo está calentando."""
    sensor = db.get(Sensor, datos.sensor_id) if datos.sensor_id else db.scalars(
        select(Sensor).where(Sensor.estado == "ACTIVO").order_by(Sensor.id)).first()
    if sensor is None:
        raise HTTPException(404, "No hay sensor activo para manipular")
    llave = llave_sensor(config.SENSOR_MASTER_KEY, sensor.id)
    resultados = [_intentar(db, {"sensor_id": sensor.id, "temperatura": 58.0, "humedad": 30.0,
                                 "globo_negro": 70.0}, llave) for _ in range(3)]
    db.refresh(sensor)
    return {"sensor_id": sensor.id, "resultados": resultados, "estado_sensor": sensor.estado}


@router.post("/fuerza-bruta", dependencies=[Depends(control_demo)])
def fuerza_bruta(db: Session = Depends(obtener_db)):
    """6 intentos de login con contraseñas comunes desde la IP FICTICIA del atacante.
    No usa la IP de quien presiona el botón, así que el expositor nunca queda bloqueado."""
    for intento in ["123456", "admin", "password", "merida2025", "protecc1on", "qwerty"]:
        registrar_evento(db, ip=IP_ATACANTE, endpoint="/api/login", tipo="LOGIN_FALLIDO",
                         motivo=f"[DEMO] Contraseña incorrecta para 'supervisor' (probó '{intento}')",
                         severidad="MEDIA")
    return {"ip_atacante": IP_ATACANTE, "intentos": 6,
            "ip_bloqueada": IP_ATACANTE in _ips_bloqueadas}


@router.post("/restablecer", dependencies=[Depends(control_demo)])
def restablecer(db: Session = Depends(obtener_db)):
    """Deja todo listo para repetir la demo desde cero:
    sin ola de calor, sin IPs bloqueadas, sin sensores en cuarentena o caídos,
    sin alertas activas y con los refugios cerrados.
    La bitácora de seguridad y el historial de alertas SE CONSERVAN (son evidencia)."""
    OLA.desactivar()
    ips = len(_ips_bloqueadas)
    _ips_bloqueadas.clear()
    GENERADOR.caido_hasta.clear()
    cuarentena = 0
    for s in db.scalars(select(Sensor).where(Sensor.estado == "CUARENTENA")):
        s.estado = "SIN_SENAL"  # vuelve a ACTIVO con su siguiente lectura
        cuarentena += 1
    db.execute(update(Refugio).values(abierto=False, ocupacion=0))
    db.commit()
    alertas = cerrar_todas(db, "Cerrada al restablecer la demo.")
    return {"restablecido": True, "ips_desbloqueadas": ips, "sensores_liberados": cuarentena,
            "alertas_cerradas": alertas, "ola_calor": False}
