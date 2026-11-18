"""
motor.py - Tarea de fondo del servidor (corre sola cada INTERVALO_SEGUNDOS).

En cada ciclo:
  1. (Si SIMULADOR_INTERNO=true) genera una ronda de lecturas de los 60 sensores.
  2. Marca como SIN_SENAL los sensores que dejaron de reportar.
  3. Calcula el estado de cada colonia (índice de calor, nivel, IRC) y lo guarda en el historial.
  4. Crea, escala o cierra la alerta de cada colonia (máximo una activa por colonia).
  5. Al amanecer simulado (6:00) cierra los refugios del día anterior.
  6. Cada 5 minutos borra las lecturas crudas de más de RETENCION_HORAS (6 h por defecto).
     Las alertas, firmas y eventos de seguridad NO se borran (son la evidencia).
"""
import asyncio
import traceback
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import config
from app.alertas import actualizar_alertas
from app.calculos import irc, nivel_alerta
from app.database import SessionLocal, ahora
from app.ingesta import recibir_lectura
from app.modelos import Colonia, EstadoColonia, Lectura, Refugio, Sensor
from app.simulacion import OLA, GeneradorLecturas, reloj

GENERADOR = GeneradorLecturas(config.DIA_SEGUNDOS)
_estado_motor = {"ciclos": 0, "dia_refugios": None}


def calcular_estados(db: Session) -> list[dict]:
    """Promedia la última lectura válida de cada sensor por colonia y guarda la "foto"."""
    ventana = ahora() - timedelta(seconds=max(15, 3 * config.INTERVALO_SEGUNDOS))
    filas = db.scalars(select(Lectura).where(
        Lectura.fecha >= ventana, Lectura.sospechosa.is_(False)).order_by(Lectura.id.desc())).all()
    ultima_por_sensor: dict[str, Lectura] = {}
    for l in filas:
        ultima_por_sensor.setdefault(l.sensor_id, l)  # la primera es la más reciente
    if not ultima_por_sensor:
        return []

    ic_ciudad = sum(l.indice_calor for l in ultima_por_sensor.values()) / len(ultima_por_sensor)
    _dia, hora = reloj(config.DIA_SEGUNDOS)
    estados = []
    for c in db.scalars(select(Colonia)):
        propias = [l for l in ultima_por_sensor.values() if l.colonia_id == c.id]
        if not propias:
            continue
        n = len(propias)
        ic = round(sum(l.indice_calor for l in propias) / n, 1)
        e = {
            "colonia_id": c.id,
            "temperatura": round(sum(l.temperatura for l in propias) / n, 1),
            "humedad": round(sum(l.humedad for l in propias) / n, 1),
            "indice_calor": ic,
            "nivel": nivel_alerta(ic),
            "irc": irc(ic, c.vulnerabilidad, c.capacidad),
            "sensores_activos": n,
        }
        db.add(EstadoColonia(fecha=ahora(), hora_simulada=round(hora, 2),
                             indice_calor_ciudad=round(ic_ciudad, 1), **e))
        estados.append(e)
    db.commit()
    return estados


def actualizar_sensores_sin_senal(db: Session) -> None:
    """Un sensor que no reporta en 30 s se considera SIN_SENAL (la cuarentena se respeta)."""
    limite = ahora() - timedelta(seconds=30)
    db.execute(update(Sensor).where(Sensor.estado == "ACTIVO", Sensor.ultima_lectura < limite)
               .values(estado="SIN_SENAL"))
    db.commit()


def cerrar_refugios_al_amanecer(db: Session) -> None:
    dia, hora = reloj(config.DIA_SEGUNDOS)
    if hora >= 6 and _estado_motor["dia_refugios"] != dia:
        _estado_motor["dia_refugios"] = dia
        db.execute(update(Refugio).values(abierto=False, ocupacion=0))
        db.commit()


def podar_datos_viejos(db: Session) -> None:
    """Borra lecturas crudas y fotos de colonias más viejas que la retención configurada."""
    limite = ahora() - timedelta(hours=config.RETENCION_HORAS)
    db.execute(delete(Lectura).where(Lectura.fecha < limite))
    db.execute(delete(EstadoColonia).where(EstadoColonia.fecha < limite))
    db.commit()


def ciclo() -> None:
    """Un ciclo completo del motor (se ejecuta en un hilo aparte para no trabar la API)."""
    db = SessionLocal()
    try:
        if config.SIMULADOR_INTERNO:
            dia, _hora = reloj(config.DIA_SEGUNDOS)
            for datos in GENERADOR.generar(OLA.extra_actual(dia)):
                try:
                    recibir_lectura(db, datos, ip="simulador-interno", endpoint="motor",
                                    confiable=True, guardar=False)
                except HTTPException:
                    pass  # ya quedó registrado como evento de seguridad
            db.commit()  # las 60 lecturas se guardan juntas
        actualizar_sensores_sin_senal(db)
        estados = calcular_estados(db)
        actualizar_alertas(db, estados)
        cerrar_refugios_al_amanecer(db)
        _estado_motor["ciclos"] += 1
        if _estado_motor["ciclos"] % 60 == 0:
            podar_datos_viejos(db)
    finally:
        db.close()


async def bucle_motor() -> None:
    """Se lanza al arrancar FastAPI y repite el ciclo para siempre."""
    print(f"[motor] Iniciado: ciclo cada {config.INTERVALO_SEGUNDOS} s, "
          f"1 día simulado = {config.DIA_SEGUNDOS} s, simulador interno = {config.SIMULADOR_INTERNO}")
    while True:
        try:
            await asyncio.to_thread(ciclo)
        except Exception:
            traceback.print_exc()  # un error en un ciclo no debe tumbar el servidor
        await asyncio.sleep(config.INTERVALO_SEGUNDOS)
