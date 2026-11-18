"""
ingesta.py - Entrada de lecturas de sensores ("telemetría").

Toda lectura pasa por estos filtros en orden, venga de la API o del simulador interno:
  1. ¿La IP está bloqueada por la regla SIEM?          -> rechazo 429
  2. ¿El sensor existe en el catálogo?                   -> evento SENSOR_DESCONOCIDO, rechazo 401
  3. ¿Su llave única es correcta?                        -> evento LLAVE_INCORRECTA, rechazo 401
  4. ¿Los valores están en rangos físicos posibles?      -> evento FUERA_DE_RANGO, rechazo 422
  5. ¿Parece manipulado (salto imposible / vecinos)?     -> se guarda marcada como sospechosa
                                                             + evento LECTURA_MANIPULADA
  6. Se calcula el índice de calor y se guarda.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.calculos import indice_calor
from app.database import ahora
from app.modelos import Lectura, Sensor
from app.seguridad import (detectar_manipulacion, error_de_rango, ip_bloqueada,
                           llave_correcta, registrar_evento)


def recibir_lectura(db: Session, datos: dict, *, ip: str, endpoint: str,
                    llave: str | None = None, confiable: bool = False, guardar: bool = True) -> dict:
    """Procesa una lectura.
    - `confiable=True` solo lo usa el simulador interno (ya está dentro del servidor, no necesita llave).
    - `guardar=False` deja el commit para después: el motor guarda las 60 lecturas de una sola vez,
      que es mucho más rápido que 60 escrituras separadas."""
    sensor_id = str(datos.get("sensor_id", ""))[:64]

    # 1. IP bloqueada
    if ip_bloqueada(ip):
        raise HTTPException(429, "IP bloqueada temporalmente por la regla de intrusión")

    # 2. Sensor conocido
    sensor = db.get(Sensor, sensor_id)
    if sensor is None:
        registrar_evento(db, ip=ip, endpoint=endpoint, tipo="SENSOR_DESCONOCIDO", sensor_id=sensor_id,
                         motivo=f"Lectura de un sensor que no está en el catálogo: '{sensor_id}'",
                         severidad="MEDIA")
        raise HTTPException(401, "Sensor no registrado")

    # 3. Llave única del sensor
    if not confiable and not llave_correcta(sensor.id, llave):
        registrar_evento(db, ip=ip, endpoint=endpoint, tipo="LLAVE_INCORRECTA", sensor_id=sensor.id,
                         motivo="Llave del sensor ausente o incorrecta (posible suplantación)",
                         severidad="ALTA")
        raise HTTPException(401, "Llave del sensor incorrecta")

    # 4. Rangos físicos
    problema = error_de_rango(datos)
    if problema:
        registrar_evento(db, ip=ip, endpoint=endpoint, tipo="FUERA_DE_RANGO", sensor_id=sensor.id,
                         motivo=problema, severidad="MEDIA")
        raise HTTPException(422, problema)

    # 5. Manipulación (y sensores en cuarentena)
    temp, hum = float(datos["temperatura"]), float(datos["humedad"])
    sospecha = None
    if sensor.estado == "CUARENTENA":
        sospecha = "Sensor en cuarentena"
    else:
        sospecha = detectar_manipulacion(db, sensor, temp)
        if sospecha:
            registrar_evento(db, ip=ip, endpoint=endpoint, tipo="LECTURA_MANIPULADA", sensor_id=sensor.id,
                             motivo=sospecha, severidad="ALTA")

    # 6. Guardar con su índice de calor
    ic = indice_calor(temp, hum)
    momento = ahora()
    db.add(Lectura(
        sensor_id=sensor.id, colonia_id=sensor.colonia_id, fecha=momento,
        temperatura=temp, humedad=hum, globo_negro=float(datos["globo_negro"]),
        indice_calor=ic, sospechosa=sospecha is not None, motivo_sospecha=sospecha,
    ))
    sensor.ultima_lectura = momento
    if sensor.estado != "CUARENTENA":
        sensor.estado = "ACTIVO"
    if guardar:
        db.commit()
    return {"aceptada": True, "sensor_id": sensor.id, "indice_calor": ic,
            "sospechosa": sospecha is not None, "motivo": sospecha}
