"""
calculos.py - Fórmulas de negocio de TermoMID (funciones puras, fáciles de probar).

1. Índice de calor (fórmula de Rothfusz, National Weather Service de EE. UU.)
2. Nivel de alerta según el índice de calor
3. IRC = Peligro × Vulnerabilidad × (1 − Capacidad)
4. Distancia entre dos puntos (para el refugio más cercano)
"""
import math

# Umbrales del proyecto (índice de calor en °C)
UMBRAL_AMARILLO = 32.0
UMBRAL_NARANJA = 39.0
UMBRAL_ROJO = 51.0

# Orden de los niveles, para comparar cuál es "mayor"
ORDEN_NIVEL = {"VERDE": 0, "AMARILLO": 1, "NARANJA": 2, "ROJO": 3}


def indice_calor(temp_c: float, humedad: float) -> float:
    """Índice de calor en °C (la "sensación térmica" por calor y humedad).

    Algoritmo oficial del NWS:
    1) Se usa una fórmula simple (Steadman). Si el resultado es menor a 80 °F, esa es la respuesta.
    2) Si no, se usa la regresión de Rothfusz (9 términos) y dos ajustes:
       - humedad muy baja (< 13 %) con calor -> se resta un poco
       - humedad muy alta (> 85 %) con 80-87 °F -> se suma un poco
    La fórmula trabaja en grados Fahrenheit, así que convertimos de ida y vuelta.
    """
    t = temp_c * 9 / 5 + 32  # °C -> °F
    rh = humedad

    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi = simple
    else:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh
              - 0.22475541 * t * rh - 0.00683783 * t * t
              - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
              + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
        if rh < 13 and 80 <= t <= 112:
            hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi += ((rh - 85) / 10) * ((87 - t) / 5)

    return round((hi - 32) * 5 / 9, 1)  # °F -> °C


def nivel_alerta(ic: float) -> str:
    """Verde < 32 °C · Amarillo 32 a 39 · Naranja 39 a 51 · Rojo ≥ 51."""
    if ic >= UMBRAL_ROJO:
        return "ROJO"
    if ic >= UMBRAL_NARANJA:
        return "NARANJA"
    if ic >= UMBRAL_AMARILLO:
        return "AMARILLO"
    return "VERDE"


def peligro(ic: float) -> float:
    """Peligro 0..1: índice de calor normalizado.
    27 °C o menos = 0 (sin peligro); 55 °C o más = 1 (peligro máximo)."""
    return round(min(1.0, max(0.0, (ic - 27.0) / (55.0 - 27.0))), 3)


def irc(ic: float, vulnerabilidad: float, capacidad: float) -> float:
    """Índice de Riesgo por Colonia = Peligro × Vulnerabilidad × (1 − Capacidad). Resultado 0..1."""
    return round(peligro(ic) * vulnerabilidad * (1 - capacidad), 3)


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos coordenadas (fórmula de Haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
