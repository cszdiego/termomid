"""
modelos.py - Tablas de la base de datos (el "ERP" de Protección Civil).

Datos maestros:  Colonia, Sensor, Refugio
Datos de operación: Lectura, EstadoColonia (historial calculado)
Flujo de trabajo: Alerta, Aprobacion, ReporteCiudadano (anónimo)
Seguridad:        EventoSeguridad, AlertaSiem

PRIVACIDAD: no existe ninguna tabla con personas. El padrón de personas mayores
que viven solas se guarda solo como un NÚMERO por colonia (sin nombre ni CURP).
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, ahora


# ---------------------------------------------------------------- DATOS MAESTROS
class Colonia(Base):
    __tablename__ = "colonias"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # ej. "centro"
    nombre: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radio_m: Mapped[int] = mapped_column(Integer)          # radio aproximado para dibujarla en el mapa
    poblacion: Mapped[int] = mapped_column(Integer)
    pct_mayores: Mapped[float] = mapped_column(Float)      # fracción de personas de 65+ años
    pct_sin_ac: Mapped[float] = mapped_column(Float)       # fracción de hogares sin aire acondicionado
    arbolado: Mapped[float] = mapped_column(Float)         # 0 = sin árboles, 1 = muy arbolada
    vulnerabilidad: Mapped[float] = mapped_column(Float)   # índice 0..1 (ver catalogos.py)
    capacidad: Mapped[float] = mapped_column(Float)        # índice 0..1 (ver catalogos.py)
    padron_mayores_solos: Mapped[int] = mapped_column(Integer)  # solo un conteo, sin datos personales


class Sensor(Base):
    __tablename__ = "sensores"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # ej. "TMID-001"
    colonia_id: Mapped[str] = mapped_column(ForeignKey("colonias.id"))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    # ACTIVO, SIN_SENAL (no ha reportado) o CUARENTENA (posible manipulación)
    estado: Mapped[str] = mapped_column(String(20), default="SIN_SENAL")
    ultima_lectura: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Refugio(Base):
    __tablename__ = "refugios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    colonia_id: Mapped[str] = mapped_column(ForeignKey("colonias.id"))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    capacidad: Mapped[int] = mapped_column(Integer)
    ocupacion: Mapped[int] = mapped_column(Integer, default=0)
    abierto: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- OPERACIÓN
class Lectura(Base):
    """Una medición cruda enviada por un sensor (o por el gateway LoRaWAN)."""
    __tablename__ = "lecturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensores.id"), index=True)
    colonia_id: Mapped[str] = mapped_column(String(40), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    temperatura: Mapped[float] = mapped_column(Float)   # °C
    humedad: Mapped[float] = mapped_column(Float)       # % humedad relativa
    globo_negro: Mapped[float] = mapped_column(Float)   # °C (temperatura de globo negro, incluye sol)
    indice_calor: Mapped[float] = mapped_column(Float)  # °C, calculado con Rothfusz
    sospechosa: Mapped[bool] = mapped_column(Boolean, default=False)  # True = no se usa en cálculos
    motivo_sospecha: Mapped[str | None] = mapped_column(String(200), nullable=True)


class EstadoColonia(Base):
    """Foto del estado de una colonia en cada ciclo del motor (sirve para gráficas y pronóstico)."""
    __tablename__ = "estado_colonias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    colonia_id: Mapped[str] = mapped_column(ForeignKey("colonias.id"), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    hora_simulada: Mapped[float] = mapped_column(Float)  # 0..24
    temperatura: Mapped[float] = mapped_column(Float)
    humedad: Mapped[float] = mapped_column(Float)
    indice_calor: Mapped[float] = mapped_column(Float)
    indice_calor_ciudad: Mapped[float] = mapped_column(Float)  # promedio de toda Mérida en ese momento
    nivel: Mapped[str] = mapped_column(String(10))
    irc: Mapped[float] = mapped_column(Float)
    sensores_activos: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- FLUJO DE ALERTAS
class Alerta(Base):
    """Alerta por colonia (como máximo UNA activa por colonia). Estados:
    PROPUESTA -> (si es roja: SEGUNDA_FIRMA) -> APROBADA -> EMITIDA -> CERRADA
    Si la colonia sube de nivel, se actualiza esta misma alerta y vuelve a PROPUESTA.
    Se CIERRA sola cuando la colonia lleva un rato en verde.
    """
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    colonia_id: Mapped[str] = mapped_column(ForeignKey("colonias.id"), index=True)
    nivel: Mapped[str] = mapped_column(String(10))
    nivel_inicial: Mapped[str] = mapped_column(String(10))
    indice_calor: Mapped[float] = mapped_column(Float)   # el máximo observado mientras estuvo activa
    irc: Mapped[float] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(20), default="PROPUESTA", index=True)
    creada: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    actualizada: Mapped[datetime] = mapped_column(DateTime, default=ahora)  # última creación/escalada
    verde_desde: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    motivo: Mapped[str] = mapped_column(String(300))
    emitida_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    emitida_por: Mapped[str | None] = mapped_column(String(40), nullable=True)
    avisos_sms: Mapped[int] = mapped_column(Integer, default=0)
    avisos_whatsapp: Mapped[int] = mapped_column(Integer, default=0)
    personas_a_llamar: Mapped[int] = mapped_column(Integer, default=0)
    refugio_id: Mapped[int | None] = mapped_column(ForeignKey("refugios.id"), nullable=True)

    aprobaciones: Mapped[list["Aprobacion"]] = relationship(
        back_populates="alerta", cascade="all, delete-orphan", lazy="selectin"
    )


class Aprobacion(Base):
    """Cada firma de un operador sobre una alerta (la roja necesita dos de personas distintas)."""
    __tablename__ = "aprobaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alerta_id: Mapped[int] = mapped_column(ForeignKey("alertas.id"), index=True)
    nivel: Mapped[str] = mapped_column(String(10))  # nivel que se aprobó (si escala, se firma de nuevo)
    usuario: Mapped[str] = mapped_column(String(40))
    rol: Mapped[str] = mapped_column(String(20))
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    alerta: Mapped[Alerta] = relationship(back_populates="aprobaciones")


class ReporteCiudadano(Base):
    """Reporte ANÓNIMO desde la app ciudadana: sin nombre, sin teléfono, solo la colonia."""
    __tablename__ = "reportes_ciudadanos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    colonia_id: Mapped[str] = mapped_column(ForeignKey("colonias.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # SINTOMAS, SIN_LUZ, SIN_AGUA, PERSONA_SOLA
    atendido: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- SEGURIDAD
class EventoSeguridad(Base):
    """Bitácora de seguridad (lo que en una empresa se mandaría al SIEM)."""
    __tablename__ = "eventos_seguridad"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    sensor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    tipo: Mapped[str] = mapped_column(String(40), index=True)  # ej. LLAVE_INCORRECTA
    motivo: Mapped[str] = mapped_column(String(300))
    severidad: Mapped[str] = mapped_column(String(10))  # BAJA, MEDIA, ALTA, CRITICA


class AlertaSiem(Base):
    """Alerta generada por una regla de correlación (varios eventos -> un incidente)."""
    __tablename__ = "alertas_siem"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    regla: Mapped[str] = mapped_column(String(60))
    severidad: Mapped[str] = mapped_column(String(10))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sensor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    descripcion: Mapped[str] = mapped_column(String(300))
    accion: Mapped[str] = mapped_column(String(200))
