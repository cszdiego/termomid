"""
catalogos.py - Datos maestros SIMULADOS de Mérida.

IMPORTANTE: todos los números son aproximados e inventados para el prototipo
(población, % de personas mayores, hogares sin aire acondicionado, arbolado,
padrón). Las coordenadas son aproximadas al centro de cada colonia. No son datos oficiales.
"""
import math
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos import Colonia, Refugio, Sensor

TOTAL_SENSORES = 60

# (id, nombre, lat, lon, radio_m, población, % mayores 65+, % hogares sin A/C, arbolado 0..1, padrón mayores solos)
COLONIAS = [
    ("centro",               "Centro",               20.9674, -89.6237,  900, 18000, 0.22, 0.40, 0.25, 210),
    ("garcia_gineres",       "García Ginerés",       20.9815, -89.6395,  900, 12000, 0.20, 0.25, 0.60,  95),
    ("itzimna",              "Itzimná",              20.9930, -89.6100,  800,  9000, 0.18, 0.20, 0.55,  60),
    ("montes_de_ame",        "Montes de Amé",        21.0390, -89.6050,  900,  8000, 0.08, 0.08, 0.45,  15),
    ("francisco_de_montejo", "Francisco de Montejo", 21.0310, -89.6560, 1200, 30000, 0.10, 0.35, 0.30, 110),
    ("ciudad_caucel",        "Ciudad Caucel",        20.9990, -89.7060, 1500, 60000, 0.05, 0.55, 0.15,  90),
    ("las_americas",         "Las Américas",         21.0450, -89.6720, 1100, 25000, 0.04, 0.50, 0.15,  40),
    ("chuburna",             "Chuburná",             21.0110, -89.6300,  900, 14000, 0.15, 0.35, 0.40,  85),
    ("pensiones",            "Pensiones",            20.9910, -89.6660, 1000, 20000, 0.14, 0.40, 0.35, 120),
    ("emiliano_zapata_sur",  "Emiliano Zapata Sur",  20.9250, -89.6150, 1200, 28000, 0.12, 0.75, 0.10, 230),
    ("san_jose_tecoh",       "San José Tecoh",       20.9320, -89.6380, 1000, 16000, 0.13, 0.70, 0.12, 170),
    ("serapio_rendon",       "Serapio Rendón",       20.9550, -89.6080,  800, 10000, 0.19, 0.60, 0.20, 140),
    ("castilla_camara",      "Castilla Cámara",      20.9560, -89.5850,  900, 15000, 0.11, 0.65, 0.15, 130),
    ("mulsay",               "Mulsay",               20.9600, -89.6680,  900, 17000, 0.12, 0.60, 0.18, 125),
    ("juan_pablo_ii",        "Juan Pablo II",        21.0060, -89.6920, 1000, 32000, 0.07, 0.55, 0.15, 105),
]

# (nombre, colonia, desplazamiento lat, desplazamiento lon, capacidad) - refugios climáticos simulados
REFUGIOS = [
    ("Centro Cultural (Centro)",                  "centro",               0.0010,  0.0015, 150),
    ("Centro Comunitario Emiliano Zapata Sur",    "emiliano_zapata_sur", -0.0020,  0.0010, 120),
    ("Unidad Deportiva San José Tecoh",           "san_jose_tecoh",       0.0015, -0.0020, 100),
    ("Centro Comunitario Castilla Cámara",        "castilla_camara",     -0.0010,  0.0020,  80),
    ("Biblioteca Pública Serapio Rendón",         "serapio_rendon",       0.0012,  0.0008,  60),
    ("Sala de enfriamiento Mulsay",               "mulsay",              -0.0015, -0.0010,  50),
    ("Centro Comunitario Ciudad Caucel",          "ciudad_caucel",        0.0020,  0.0025, 120),
    ("Polideportivo Juan Pablo II",               "juan_pablo_ii",       -0.0010,  0.0015, 100),
    ("Casa de la Cultura Pensiones",              "pensiones",            0.0010, -0.0012,  70),
    ("Centro Comunitario Francisco de Montejo",   "francisco_de_montejo", -0.0018, 0.0010,  90),
]


def calcular_vulnerabilidad(pct_mayores: float, pct_sin_ac: float) -> float:
    """Vulnerabilidad 0..1 = mitad por personas mayores + mitad por hogares sin aire acondicionado.
    Un 25 % de mayores o más se considera el máximo (vale 1)."""
    return round(0.5 * min(1.0, pct_mayores / 0.25) + 0.5 * pct_sin_ac, 3)


def calcular_capacidad(arbolado: float, num_refugios: int) -> float:
    """Capacidad de respuesta 0..1 = 70 % arbolado (sombra) + 30 % refugios (2 o más = máximo)."""
    return round(0.7 * arbolado + 0.3 * min(1.0, num_refugios / 2), 3)


def repartir_sensores() -> dict[str, int]:
    """Reparte los 60 sensores: 2 por colonia como base y los 30 restantes
    en proporción a la vulnerabilidad (principio ético: más sensores donde hay más riesgo).
    Usa el método del "mayor residuo" para que la suma dé exactamente 60."""
    base = 2
    restantes = TOTAL_SENSORES - base * len(COLONIAS)
    vulns = {c[0]: calcular_vulnerabilidad(c[6], c[7]) for c in COLONIAS}
    total_v = sum(vulns.values())
    cuotas = {cid: restantes * v / total_v for cid, v in vulns.items()}
    reparto = {cid: base + math.floor(q) for cid, q in cuotas.items()}
    faltan = TOTAL_SENSORES - sum(reparto.values())
    # Los que tuvieron mayor parte decimal reciben uno extra
    for cid in sorted(cuotas, key=lambda k: cuotas[k] - math.floor(cuotas[k]), reverse=True)[:faltan]:
        reparto[cid] += 1
    return reparto


def lista_sensores() -> list[dict]:
    """Genera la lista de sensores de forma DETERMINISTA (siempre los mismos IDs y posiciones).
    Así el servidor y el script externo simulador.py conocen los mismos sensores."""
    reparto = repartir_sensores()
    sensores = []
    numero = 1
    for (cid, _nombre, lat, lon, radio, *_resto) in COLONIAS:
        azar = random.Random(cid)  # semilla fija = mismas posiciones siempre
        for _ in range(reparto[cid]):
            # Posición aleatoria dentro del 70 % del radio de la colonia (1° de latitud ≈ 111 km)
            dist = radio * 0.7 * math.sqrt(azar.random()) / 111_000
            ang = azar.uniform(0, 2 * math.pi)
            sensores.append({
                "id": f"TMID-{numero:03d}",
                "colonia_id": cid,
                "lat": round(lat + dist * math.sin(ang), 5),
                "lon": round(lon + dist * math.cos(ang), 5),
            })
            numero += 1
    return sensores


def sembrar_datos(db: Session) -> None:
    """Carga los datos maestros la primera vez que arranca la app (si la BD está vacía)."""
    if db.scalar(select(Colonia.id).limit(1)):
        return  # ya hay datos

    refugios_por_colonia = {}
    for _n, cid, *_ in REFUGIOS:
        refugios_por_colonia[cid] = refugios_por_colonia.get(cid, 0) + 1

    centros = {}
    for (cid, nombre, lat, lon, radio, pob, mayores, sin_ac, arbolado, padron) in COLONIAS:
        centros[cid] = (lat, lon)
        db.add(Colonia(
            id=cid, nombre=nombre, lat=lat, lon=lon, radio_m=radio, poblacion=pob,
            pct_mayores=mayores, pct_sin_ac=sin_ac, arbolado=arbolado,
            vulnerabilidad=calcular_vulnerabilidad(mayores, sin_ac),
            capacidad=calcular_capacidad(arbolado, refugios_por_colonia.get(cid, 0)),
            padron_mayores_solos=padron,
        ))
    db.flush()  # las colonias deben existir antes que sensores y refugios

    for s in lista_sensores():
        db.add(Sensor(**s))

    for nombre, cid, dlat, dlon, cap in REFUGIOS:
        lat, lon = centros[cid]
        db.add(Refugio(nombre=nombre, colonia_id=cid, lat=lat + dlat, lon=lon + dlon,
                       capacidad=cap, ocupacion=0, abierto=False))
    db.commit()
    print(f"[catalogos] Datos maestros cargados: {len(COLONIAS)} colonias, "
          f"{TOTAL_SENSORES} sensores, {len(REFUGIOS)} refugios.")
