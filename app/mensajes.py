"""
mensajes.py - Textos para la población, en español y maya.

IMPORTANTE: los textos en maya son una traducción breve de apoyo para el prototipo.
Antes de usarlos de verdad debe validarlos un hablante nativo (por ejemplo, INDEMAYA).
"""

RECOMENDACIONES = {
    "VERDE": {
        "es": "Condiciones normales. Toma agua durante el día.",
        "maya": "Ma'alob. Uk'a' ja'.",
    },
    "AMARILLO": {
        "es": "Precaución: toma agua seguido, usa ropa ligera y evita el sol de 12 a 4 de la tarde.",
        "maya": "Kanáantaba. Uk'a' ja' yaab téenak.",
    },
    "NARANJA": {
        "es": "Peligro: no salgas de 11 a 5 de la tarde, busca un lugar fresco y revisa a las personas mayores.",
        "maya": "Sajbe'entsil. Ma' a jóok'ol tu chúumuk k'iin. Kanáant a nojoch máakilo'ob.",
    },
    "ROJO": {
        "es": "Peligro extremo: quédate en un lugar fresco o acude al refugio climático. Si alguien se siente mal, llama al 911.",
        "maya": "Jach sajbe'entsil. Xeen ti' le kúuchil síis. Wa yaan máax k'oja'an, t'aan 911.",
    },
}

# Consejos cortos con icono para la app ciudadana (iguales en todos los niveles con alerta)
CONSEJOS = [
    {"icono": "agua", "es": "Toma agua cada hora aunque no tengas sed", "maya": "Uk'a' ja' yaab téenak"},
    {"icono": "sol", "es": "Evita el sol de 12 a 17 h", "maya": "Ma' a jóok'ol tu chúumuk k'iin"},
    {"icono": "tel", "es": "Llama a tus vecinos y familiares mayores", "maya": "T'aan a nojoch máakilo'ob"},
]

# "Alerta" es femenino: alerta roja, alerta amarilla
ALERTA_NIVEL = {"VERDE": "VERDE", "AMARILLO": "AMARILLA", "NARANJA": "NARANJA", "ROJO": "ROJA"}
NOMBRE_NIVEL_MAYA = {"VERDE": "Ya'ax", "AMARILLO": "K'an", "NARANJA": "K'an-chak", "ROJO": "Chak"}


def mensaje_alerta(nivel: str, colonia: str, indice_calor: float) -> dict:
    """Texto del SMS/WhatsApp simulado (vista previa antes de emitir)."""
    rec = RECOMENDACIONES[nivel]
    return {
        "titulo": f"ALERTA {ALERTA_NIVEL[nivel]} POR CALOR · TermoMID",
        "es": f"{colonia}: índice de calor de hasta {indice_calor:.0f} °C. {rec['es']}",
        "maya": f"{NOMBRE_NIVEL_MAYA[nivel].upper()} · TermoMID. {colonia}: {indice_calor:.0f} °C. {rec['maya']}",
    }


# Reportes anónimos de la app ciudadana
TIPOS_REPORTE = {
    "SINTOMAS": "Alguien tiene síntomas por calor",
    "SIN_LUZ": "Se fue la luz",
    "SIN_AGUA": "Punto de hidratación sin agua",
    "PERSONA_SOLA": "Persona mayor sola que necesita visita",
}
