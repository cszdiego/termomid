"""
database.py - Conexión a la base de datos con SQLAlchemy.

El mismo código funciona con SQLite (local) y PostgreSQL (nube); solo cambia
la URL que viene de la variable DATABASE_URL.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL, ZONA_HORARIA

if DATABASE_URL.startswith("sqlite"):
    # SQLite: permitimos usar la conexión desde varios hilos (la API y el motor de fondo)
    # y esperamos hasta 30 s si otro hilo está escribiendo.
    engine = create_engine(
        DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def _ajustes_sqlite(conexion, _registro):
        # WAL permite leer mientras otro hilo escribe; NORMAL evita esperar al disco en cada commit.
        cursor = conexion.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
else:
    # PostgreSQL: pool_pre_ping revisa que la conexión siga viva antes de usarla.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)

# Fábrica de sesiones: cada petición o ciclo del motor abre su propia sesión.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Clase base de la que heredan todas las tablas (ver modelos.py)."""


def ahora() -> datetime:
    """Hora actual en UTC, sin zona horaria (así SQLite y PostgreSQL la guardan igual)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


MERIDA = ZoneInfo(ZONA_HORARIA)


def iso(fecha: datetime | None) -> str | None:
    """Convierte una fecha guardada en UTC a texto en hora de Mérida, con su desfase.
    Ejemplo: 2026-05-12T22:40:00 (UTC) -> "2026-05-12T16:40:00-06:00".
    El desfase permite que cualquier programa sepa exactamente a qué instante se refiere."""
    if fecha is None:
        return None
    return fecha.replace(tzinfo=timezone.utc).astimezone(MERIDA).isoformat(timespec="seconds")


def obtener_db():
    """Dependencia de FastAPI: abre una sesión por petición y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
