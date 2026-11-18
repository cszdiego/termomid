"""
pronostico.py - Pronóstico del índice de calor por colonia a +24, +48 y +72 horas.

Método (simple y explicable):
    pronóstico_colonia(hora) = pronóstico_regional(hora) + ajuste_colonia

- pronóstico_regional(hora): índice de calor esperado para el promedio de Mérida a esa hora.
  En el prototipo sale del modelo climático simulado (en la vida real vendría del
  Servicio Meteorológico Nacional o de un modelo numérico como GFS).
- ajuste_colonia: cuánto más caliente (o fresca) ha estado la colonia respecto
  al promedio de la ciudad en las últimas mediciones. Se calcula con los datos
  de los sensores (tabla estado_colonias).
- El pronóstico a +24 h es el MÁXIMO del día siguiente (lo que importa para alertar).

En PRODUCCIÓN se usaría:
- Kriging (interpolación geoestadística) para estimar el calor entre sensores.
- LightGBM (modelo de machine learning) entrenado con historial de sensores,
  pronóstico regional, arbolado, densidad urbana, etc.
"""
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.calculos import indice_calor, nivel_alerta
from app.modelos import Colonia, EstadoColonia
from app.simulacion import OLA, clima_regional, reloj

HORIZONTES = {"+24h": 1, "+48h": 2, "+72h": 3}  # días hacia adelante
MUESTRAS_AJUSTE = 60  # últimos 60 ciclos (5 min reales con ciclos de 5 s ≈ más de 1 día simulado)


def regional_a_la_hora(dia: int, hora: float) -> float:
    """Índice de calor regional pronosticado para un día y hora simulados."""
    temp, hum = clima_regional(hora, dia, OLA.extra(dia))
    return indice_calor(temp, hum)


def pronostico_regional(dia: int) -> float:
    """Índice de calor MÁXIMO regional de un día (el mayor de sus 24 horas)."""
    return max(regional_a_la_hora(dia, h) for h in range(24))


def ajuste_colonia(db: Session, colonia_id: str) -> float:
    """Diferencia promedio (colonia − ciudad) en las últimas mediciones."""
    filas = db.execute(select(EstadoColonia.indice_calor, EstadoColonia.indice_calor_ciudad)
                       .where(EstadoColonia.colonia_id == colonia_id)
                       .order_by(EstadoColonia.id.desc()).limit(MUESTRAS_AJUSTE)).all()
    if not filas:
        return 0.0
    return round(sum(c - r for c, r in filas) / len(filas), 2)


def pronostico_colonias(db: Session) -> dict[str, dict]:
    """Pronóstico de todas las colonias: {colonia_id: {"ajuste", "+24h": {...}, ...}}."""
    dia_hoy, _hora = reloj(config.DIA_SEGUNDOS)
    regional = {h: pronostico_regional(dia_hoy + n) for h, n in HORIZONTES.items()}
    resultado = {}
    for cid in db.scalars(select(Colonia.id)):
        ajuste = ajuste_colonia(db, cid)
        resultado[cid] = {"ajuste": ajuste}
        for h in HORIZONTES:
            ic = round(regional[h] + ajuste, 1)
            resultado[cid][h] = {"indice_calor": ic, "nivel": nivel_alerta(ic),
                                 "regional": regional[h]}
    return resultado


def curva_colonia(db: Session, colonia_id: str, horas: int = 72) -> list[dict]:
    """Pronóstico hora por hora (horas simuladas) desde ahora hasta +72 h, para la gráfica."""
    ajuste = ajuste_colonia(db, colonia_id)
    segundos_por_hora = config.DIA_SEGUNDOS / 24
    ahora_real = time.time()
    puntos = []
    for h in range(horas + 1):
        dia, hora = reloj(config.DIA_SEGUNDOS, ahora_real + h * segundos_por_hora)
        ic = round(regional_a_la_hora(dia, hora) + ajuste, 1)
        puntos.append({"h": h, "hora_del_dia": round(hora, 1), "indice_calor": ic, "nivel": nivel_alerta(ic)})
    return puntos
