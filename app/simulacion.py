"""
simulacion.py - Modelo SIMULADO del clima de Mérida y generador de lecturas de sensores.

Lo usan dos "clientes":
- El motor del servidor (motor.py), para que en la nube se generen datos solos.
- El script externo simulador.py, que manda las lecturas por HTTPS como un gateway LoRaWAN.

Este archivo NO lee secretos ni la base de datos, para que el script externo pueda usarlo.
"""
import math
import random
import time

from app.catalogos import COLONIAS, lista_sensores

# ---------------------------------------------------------------- CLIMA BASE (temporada de calor)
# Temperatura: mínima ~24.5 °C al amanecer y máxima ~35.5 °C a las 15:00 (promedio de la ciudad).
T_MEDIA, T_AMPLITUD = 30.0, 5.5
# Humedad: ~85 % de madrugada y ~40 % a las 15:00.
H_MEDIA, H_AMPLITUD = 62.5, 22.5
HORA_MAS_CALIENTE = 15
# Grados extra que agrega una "ola de calor" (se activa desde los controles de demo).
EXTRA_OLA_CALOR = 5.0


def reloj(dia_segundos: int, t: float | None = None) -> tuple[int, float]:
    """Reloj simulado comprimido: devuelve (número de día, hora 0..24).

    Se calcula a partir de la hora real, así el servidor y el script externo
    obtienen la misma hora simulada sin tener que comunicarse.
    Ejemplo: con 240 s por día, 10 s reales = 1 hora simulada.
    """
    dias = (time.time() if t is None else t) / dia_segundos
    dia = int(dias)
    return dia, (dias - dia) * 24


def variacion_dia(dia: int) -> float:
    """Cada día es un poco distinto (±1.2 °C). Es determinista para que el pronóstico la "conozca"."""
    return 1.2 * math.sin(dia * 1.7)


def clima_regional(hora: float, dia: int, extra_ola: float = 0.0) -> tuple[float, float]:
    """Temperatura y humedad promedio de Mérida para una hora del día simulado."""
    ciclo = math.cos(2 * math.pi * (hora - HORA_MAS_CALIENTE) / 24)  # 1 a las 15 h, -1 a las 3 h
    temp = T_MEDIA + T_AMPLITUD * ciclo + variacion_dia(dia) + extra_ola
    hum = H_MEDIA - H_AMPLITUD * ciclo
    return temp, hum


def _desfases_colonias() -> dict[str, float]:
    """Diferencia térmica de cada colonia respecto al promedio de la ciudad.

    - El sur es más caliente que el norte (~4 °C por cada 0.1° de latitud).
    - Las colonias con más árboles son un poco más frescas.
    Se centra en cero para que el promedio de la ciudad no cambie.
    Resultado: unos 5 °C de diferencia entre la colonia más fresca y la más caliente.
    """
    crudos = {}
    for (cid, _n, lat, _lon, _r, _pob, _may, _ac, arbolado, _pad) in COLONIAS:
        crudos[cid] = 40 * (21.0 - lat) + 2.0 * (0.5 - arbolado)
    promedio = sum(crudos.values()) / len(crudos)
    return {cid: round(v - promedio, 2) for cid, v in crudos.items()}


DESFASE_COLONIA = _desfases_colonias()


# ---------------------------------------------------------------- OLA DE CALOR (estado de la demo)
class EstadoOla:
    """Guarda si hay una ola de calor activa y en qué días simulados dura."""

    def __init__(self):
        self.dia_inicio: int | None = None
        self.dia_fin: int | None = None  # el último día con ola (inclusive)
        self.rampa_seg = 30.0            # segundos reales que tarda en pasar de 0 a +5 °C (o al revés)
        self._actual = 0.0               # grados extra que se están aplicando ahora mismo
        self._ultimo = time.time()

    def activar(self, dia_actual: int, duracion_dias: int = 3, rampa_seg: float = 30) -> None:
        self.dia_inicio = dia_actual
        self.dia_fin = dia_actual + duracion_dias - 1
        self.rampa_seg = max(1.0, rampa_seg)

    def desactivar(self) -> None:
        self.dia_inicio = self.dia_fin = None

    def extra(self, dia: int) -> float:
        """Grados extra previstos para un día (lo usa el pronóstico; 0 si no hay ola ese día)."""
        if self.dia_inicio is not None and self.dia_inicio <= dia <= self.dia_fin:
            return EXTRA_OLA_CALOR
        return 0.0

    def extra_actual(self, dia: int) -> float:
        """Grados extra AHORA. Se acercan poco a poco al objetivo (en rampa), tanto al empezar
        como al terminar la ola, como en la realidad. Un cambio de 5 °C de golpe se parecería
        a un sensor manipulado y dispararía la detección."""
        ahora = time.time()
        paso_max = EXTRA_OLA_CALOR * (ahora - self._ultimo) / self.rampa_seg
        objetivo = self.extra(dia)
        self._actual += max(-paso_max, min(paso_max, objetivo - self._actual))
        self._ultimo = ahora
        return round(self._actual, 2)


OLA = EstadoOla()  # una sola instancia compartida en el servidor


# ---------------------------------------------------------------- GENERADOR DE LECTURAS
class GeneradorLecturas:
    """Produce una lectura por sensor en cada ronda, con fallas aleatorias."""

    PROB_CAIDA = 0.002   # probabilidad por ronda de que un sensor se caiga (sin señal)
    PROB_RUIDO = 0.03    # probabilidad de una lectura con ruido (±1.5 °C)

    def __init__(self, dia_segundos: int):
        self.dia_segundos = dia_segundos
        self.sensores = lista_sensores()
        self.caido_hasta: dict[str, float] = {}  # sensor_id -> hora real en que vuelve
        # Cada sensor tiene un pequeño sesgo propio (ningún sensor es idéntico a otro)
        azar = random.Random(42)
        self.sesgo = {s["id"]: azar.uniform(-0.3, 0.3) for s in self.sensores}

    def tumbar(self, sensor_id: str, segundos: int = 120) -> None:
        """Simula que un sensor se queda sin batería o sin señal LoRa."""
        self.caido_hasta[sensor_id] = time.time() + segundos

    def esta_caido(self, sensor_id: str) -> bool:
        return self.caido_hasta.get(sensor_id, 0) > time.time()

    def generar(self, extra_ola_hoy: float = 0.0) -> list[dict]:
        """Genera las lecturas de esta ronda (una por sensor que esté funcionando)."""
        dia, hora = reloj(self.dia_segundos)
        t_reg, h_reg = clima_regional(hora, dia, extra_ola_hoy)
        # Radiación solar (0 de noche, 1 al mediodía): calienta el globo negro
        sol = max(0.0, math.sin(math.pi * (hora - 6) / 12))

        lecturas = []
        for s in self.sensores:
            sid = s["id"]
            # Falla aleatoria: el sensor deja de transmitir entre 20 y 90 segundos
            if random.random() < self.PROB_CAIDA:
                self.tumbar(sid, random.randint(20, 90))
            if self.esta_caido(sid):
                continue

            desfase = DESFASE_COLONIA[s["colonia_id"]]
            temp = t_reg + desfase + self.sesgo[sid] + random.gauss(0, 0.2)
            if random.random() < self.PROB_RUIDO:
                temp += random.uniform(-1.5, 1.5)  # lectura con ruido
            # Las zonas más calientes son un poco más secas
            hum = min(100.0, max(15.0, h_reg - desfase * 2 + random.gauss(0, 1.0)))
            globo = temp + 8 * sol + random.gauss(0, 0.3)

            lecturas.append({
                "sensor_id": sid,
                "temperatura": round(temp, 2),
                "humedad": round(hum, 1),
                "globo_negro": round(globo, 2),
            })
        return lecturas
