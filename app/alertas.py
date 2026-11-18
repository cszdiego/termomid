"""
alertas.py - Flujo de trabajo de alertas (como un ERP: propuesta -> aprobación -> emisión).

"El modelo propone, una persona aprueba":
  1. PROPUESTA      El motor la crea sola cuando una colonia llega a AMARILLO o más.
  2. SEGUNDA_FIRMA  (solo ROJO) ya firmó un operador; falta otro operador DISTINTO.
  3. APROBADA       Lista para emitirse.
  4. EMITIDA        Se mandan avisos (SMS/WhatsApp simulados), se llama al padrón y se abre refugio.
  5. CERRADA        La colonia lleva un rato en VERDE (o se restableció la demo).

Reglas para no duplicar:
  - Como máximo UNA alerta activa por colonia.
  - Si la colonia SUBE de nivel, se actualiza esa misma alerta y regresa a PROPUESTA:
    hay que volver a aprobarla para el nuevo nivel (si sube a ROJO, con doble firma).
  - Si BAJA sin llegar a verde, la alerta se queda en su nivel más alto (criterio prudente).
  - Si pasa a VERDE y se mantiene así 3 horas simuladas, la alerta se cierra sola.

Qué pasa al EMITIR, según el nivel:
  AMARILLO -> solo avisos a la población.
  NARANJA  -> avisos + llamadas al padrón de personas mayores que viven solas + abrir refugio.
  ROJO     -> igual que naranja, pero con doble firma previa.
"""
import math
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.calculos import ORDEN_NIVEL, distancia_km
from app.database import ahora
from app.modelos import Alerta, Aprobacion, Colonia, Refugio

ACTIVAS = ("PROPUESTA", "SEGUNDA_FIRMA", "APROBADA", "EMITIDA")

# Simulación de alcance de los avisos (fracción de la población que los recibe)
ALCANCE_SMS = 0.45
ALCANCE_WHATSAPP = 0.35


def tiempo_para_cerrar() -> timedelta:
    """Cuánto tiempo seguido en verde cierra una alerta: 3 horas simuladas (1/8 de día)."""
    return timedelta(seconds=config.DIA_SEGUNDOS / 8)


def firmas_requeridas(nivel: str) -> int:
    return 2 if nivel == "ROJO" else 1


def alerta_activa(db: Session, colonia_id: str) -> Alerta | None:
    return db.scalars(select(Alerta).where(Alerta.colonia_id == colonia_id, Alerta.estado.in_(ACTIVAS))
                      .order_by(Alerta.id.desc()).limit(1)).first()


def actualizar_alertas(db: Session, estados: list[dict]) -> None:
    """Revisa el estado actual de cada colonia: crea, escala o cierra su alerta."""
    momento = ahora()
    for e in estados:
        nivel, ic, irc = e["nivel"], e["indice_calor"], e["irc"]
        activa = alerta_activa(db, e["colonia_id"])

        # --- La colonia está en VERDE: si tiene alerta, contamos cuánto tiempo lleva así
        if ORDEN_NIVEL[nivel] < ORDEN_NIVEL["AMARILLO"]:
            if activa:
                if activa.verde_desde is None:
                    activa.verde_desde = momento
                elif momento - activa.verde_desde >= tiempo_para_cerrar():
                    activa.estado = "CERRADA"
                    activa.cerrada_en = momento
                    activa.motivo = (activa.motivo + " | Cerrada: la colonia volvió a VERDE.")[:300]
            continue

        # --- AMARILLO o más
        if activa is None:
            db.add(Alerta(
                colonia_id=e["colonia_id"], nivel=nivel, nivel_inicial=nivel, indice_calor=ic, irc=irc,
                estado="PROPUESTA", creada=momento, actualizada=momento,
                motivo=f"Índice de calor {ic:.1f} °C ({nivel}), IRC {irc:.2f}. "
                       f"Propuesta automática del modelo; requiere aprobación humana.",
            ))
            continue

        activa.verde_desde = None  # ya no está en verde
        if ORDEN_NIVEL[nivel] > ORDEN_NIVEL[activa.nivel]:
            # ESCALADA: misma alerta, nuevo nivel, hay que aprobarla otra vez
            anterior = activa.nivel
            activa.nivel = nivel
            activa.estado = "PROPUESTA"
            activa.actualizada = momento
            activa.motivo = (f"Escaló de {anterior} a {nivel}: índice de calor {ic:.1f} °C, IRC {irc:.2f}. "
                             f"Requiere nueva aprobación"
                             + (" con doble firma." if nivel == "ROJO" else "."))
        activa.indice_calor = max(activa.indice_calor, ic)
        activa.irc = max(activa.irc, irc)
    db.commit()


def firmas_del_nivel(alerta: Alerta) -> list[Aprobacion]:
    """Solo cuentan las firmas dadas para el nivel ACTUAL (las de un nivel anterior quedan de historial)."""
    return [ap for ap in alerta.aprobaciones if ap.nivel == alerta.nivel]


def aprobar(db: Session, alerta: Alerta, usuario: dict) -> Alerta:
    """Un operador firma la alerta. La ROJA necesita dos firmas de personas distintas."""
    if alerta.estado not in ("PROPUESTA", "SEGUNDA_FIRMA"):
        raise HTTPException(409, f"La alerta está {alerta.estado}; no se puede aprobar")
    firmas = firmas_del_nivel(alerta)
    if any(ap.usuario == usuario["usuario"] for ap in firmas):
        raise HTTPException(409, "Ya firmaste esta alerta. La segunda firma debe ser de OTRA persona.")

    alerta.aprobaciones.append(Aprobacion(nivel=alerta.nivel, usuario=usuario["usuario"],
                                          rol=usuario["rol"], fecha=ahora()))
    if len(firmas) + 1 < firmas_requeridas(alerta.nivel):
        alerta.estado = "SEGUNDA_FIRMA"
    else:
        alerta.estado = "APROBADA"
    db.commit()
    return alerta


def refugio_mas_cercano(db: Session, colonia: Colonia) -> Refugio | None:
    """El refugio con lugares libres más cercano al centro de la colonia."""
    con_lugar = [r for r in db.scalars(select(Refugio)) if r.ocupacion < r.capacidad]
    if not con_lugar:
        return None
    return min(con_lugar, key=lambda r: distancia_km(colonia.lat, colonia.lon, r.lat, r.lon))


def emitir(db: Session, alerta: Alerta, usuario: dict) -> Alerta:
    """Emite el aviso a la población (todo SIMULADO: no se manda ningún SMS real).
    Si la alerta ya se había emitido en un nivel menor, los avisos se suman."""
    if alerta.estado != "APROBADA":
        raise HTTPException(409, f"Solo se emiten alertas APROBADAS (esta está {alerta.estado})")
    colonia = db.get(Colonia, alerta.colonia_id)

    alerta.avisos_sms += round(colonia.poblacion * ALCANCE_SMS)
    alerta.avisos_whatsapp += round(colonia.poblacion * ALCANCE_WHATSAPP)
    if ORDEN_NIVEL[alerta.nivel] >= ORDEN_NIVEL["NARANJA"]:
        alerta.personas_a_llamar = colonia.padron_mayores_solos
        refugio = db.get(Refugio, alerta.refugio_id) if alerta.refugio_id else refugio_mas_cercano(db, colonia)
        if refugio:
            if not alerta.refugio_id:
                # Se estima que ~30 % del padrón (mínimo 5 personas) llega al refugio
                llegan = max(5, math.ceil(colonia.padron_mayores_solos * 0.3))
                refugio.ocupacion = min(refugio.capacidad, refugio.ocupacion + llegan)
                alerta.refugio_id = refugio.id
            refugio.abierto = True

    alerta.estado = "EMITIDA"
    alerta.emitida_en = ahora()
    alerta.emitida_por = usuario["usuario"]
    db.commit()
    return alerta


def abrir_refugio(db: Session, refugio: Refugio) -> Refugio:
    """Apertura manual de un refugio por parte de un operador."""
    refugio.abierto = True
    db.commit()
    return refugio


def cerrar_todas(db: Session, motivo: str) -> int:
    """Cierra todas las alertas activas (lo usa el botón "restablecer" de la demo)."""
    activas = db.scalars(select(Alerta).where(Alerta.estado.in_(ACTIVAS))).all()
    for a in activas:
        a.estado = "CERRADA"
        a.cerrada_en = ahora()
        a.motivo = (a.motivo + f" | {motivo}")[:300]
    db.commit()
    return len(activas)
