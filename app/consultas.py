"""
consultas.py - Consultas de lectura que arman lo que muestra el dashboard (KPIs, colonias).
"""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.calculos import ORDEN_NIVEL
from app.database import ahora
from app.modelos import Alerta, Colonia, EstadoColonia, Refugio, Sensor
from app.pronostico import pronostico_colonias
from app.simulacion import OLA, reloj


def ultimos_estados(db: Session) -> dict[str, EstadoColonia]:
    """El estado más reciente de cada colonia (la última "foto" del motor)."""
    ultimos = select(func.max(EstadoColonia.id)).group_by(EstadoColonia.colonia_id)
    filas = db.scalars(select(EstadoColonia).where(EstadoColonia.id.in_(ultimos)))
    return {e.colonia_id: e for e in filas}


def resumen_colonias(db: Session) -> list[dict]:
    """Cada colonia con sus datos maestros, estado actual y pronóstico, ordenadas por riesgo (IRC)."""
    estados = ultimos_estados(db)
    pron = pronostico_colonias(db)
    salida = []
    for c in db.scalars(select(Colonia)):
        e = estados.get(c.id)
        salida.append({
            "id": c.id, "nombre": c.nombre, "lat": c.lat, "lon": c.lon, "radio_m": c.radio_m,
            "poblacion": c.poblacion, "vulnerabilidad": c.vulnerabilidad, "capacidad": c.capacidad,
            "padron_mayores_solos": c.padron_mayores_solos,
            "mayores_65": round(c.poblacion * c.pct_mayores),
            "ahora": None if e is None else {
                "indice_calor": e.indice_calor, "temperatura": e.temperatura, "humedad": e.humedad,
                "nivel": e.nivel, "irc": e.irc, "sensores_activos": e.sensores_activos,
            },
            "pronostico": pron.get(c.id),
        })
    salida.sort(key=lambda x: x["ahora"]["irc"] if x["ahora"] else -1, reverse=True)
    return salida


def kpis(db: Session, colonias: list[dict] | None = None) -> dict:
    """Indicadores del centro de mando. Puede recibir el resumen de colonias ya calculado."""
    if colonias is None:
        colonias = resumen_colonias(db)
    con_datos = [c for c in colonias if c["ahora"]]

    nivel_manana = "VERDE"
    for c in colonias:
        n = c["pronostico"]["+24h"]["nivel"]
        if ORDEN_NIVEL[n] > ORDEN_NIVEL[nivel_manana]:
            nivel_manana = n

    max_col = max(con_datos, key=lambda c: c["ahora"]["indice_calor"], default=None)
    max_manana = max(colonias, key=lambda c: c["pronostico"]["+24h"]["indice_calor"], default=None)
    naranja_rojo_manana = sum(1 for c in colonias
                              if ORDEN_NIVEL[c["pronostico"]["+24h"]["nivel"]] >= ORDEN_NIVEL["NARANJA"])
    # Personas vulnerables expuestas = mayores de 65 en colonias que ahora están en NARANJA o ROJO
    expuestas = [c for c in con_datos if ORDEN_NIVEL[c["ahora"]["nivel"]] >= ORDEN_NIVEL["NARANJA"]]

    hace_30s = ahora() - timedelta(seconds=30)
    activos = db.scalar(select(func.count()).select_from(Sensor).where(
        Sensor.estado == "ACTIVO", Sensor.ultima_lectura >= hace_30s))
    dia, hora = reloj(config.DIA_SEGUNDOS)

    return {
        "hora_simulada": round(hora, 2),
        "dia_simulado": dia,
        "ola_calor": OLA.extra(dia) > 0,
        "nivel_manana": nivel_manana,
        "indice_calor_max": max_col["ahora"]["indice_calor"] if max_col else None,
        "colonia_mas_caliente": max_col["nombre"] if max_col else None,
        "indice_calor_max_manana": max_manana["pronostico"]["+24h"]["indice_calor"] if max_manana else None,
        "colonia_mas_caliente_manana": max_manana["nombre"] if max_manana else None,
        "colonias_naranja_rojo_manana": naranja_rojo_manana,
        "colonias_total": len(colonias),
        "rojas_por_aprobar": db.scalar(select(func.count()).select_from(Alerta).where(
            Alerta.nivel == "ROJO", Alerta.estado.in_(("PROPUESTA", "SEGUNDA_FIRMA")))),
        "personas_vulnerables_expuestas": sum(c["mayores_65"] for c in expuestas),
        "padron_expuesto": sum(c["padron_mayores_solos"] for c in expuestas),
        "sensores_activos": activos,
        "sensores_total": db.scalar(select(func.count()).select_from(Sensor)),
        "sensores_cuarentena": db.scalar(select(func.count()).select_from(Sensor).where(Sensor.estado == "CUARENTENA")),
        "refugios_abiertos": db.scalar(select(func.count()).select_from(Refugio).where(Refugio.abierto.is_(True))),
        "alertas_pendientes": db.scalar(select(func.count()).select_from(Alerta).where(
            Alerta.estado.in_(("PROPUESTA", "SEGUNDA_FIRMA")))),
    }
