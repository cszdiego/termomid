"""
main.py - Punto de entrada de TermoMID (FastAPI).

Ejecutar en local:   uvicorn app.main:app --reload
Documentación viva:  http://127.0.0.1:8000/docs
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import modelos  # noqa: F401  (importarlo registra las tablas en Base)
from app.catalogos import sembrar_datos
from app.database import Base, SessionLocal, engine
from app.motor import bucle_motor
from app.rutas_demo import router as rutas_demo
from app.rutas_operacion import router as rutas_operacion
from app.rutas_publicas import router as rutas_publicas
from app.rutas_seguridad import router as rutas_seguridad


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Al arrancar: crea las tablas, carga los catálogos y lanza el motor de fondo."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        sembrar_datos(db)
    tarea = asyncio.create_task(bucle_motor())
    yield
    tarea.cancel()  # al apagar el servidor, detenemos el motor


app = FastAPI(
    title="TermoMID API",
    description="Sistema de alerta temprana por calor extremo, colonia por colonia, en Mérida. "
                "Prototipo alfa académico: **todos los datos son SIMULADOS**.",
    version="0.1.0",
    lifespan=ciclo_de_vida,
)


@app.middleware("http")
async def encabezados_seguridad(request: Request, call_next):
    """Encabezados HTTP de seguridad en todas las respuestas:
    - X-Content-Type-Options: el navegador no "adivina" tipos de archivo.
    - X-Frame-Options: nadie puede meter nuestra página en un iframe (clickjacking).
    - Content-Security-Policy: solo se ejecutan scripts de nuestro servidor y de los CDN
      que usamos (Leaflet y Chart.js). Es una segunda barrera contra XSS.
      /docs se excluye porque Swagger necesita scripts en línea.
    """
    respuesta = await call_next(request)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    # A otros sitios solo se envía el dominio (sin la ruta). Los servidores de mapas exigen
    # el Referer: con "no-referrer" bloqueaban las teselas (error 403).
    respuesta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        # Leaflet y Chart.js están DENTRO del proyecto (app/static/vendor), así que solo se
        # permiten scripts y estilos de nuestro propio servidor. Lo único externo son las
        # imágenes del mapa base de OpenStreetMap.
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https://tile.openstreetmap.org; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'"
        )
    return respuesta


app.include_router(rutas_publicas)
app.include_router(rutas_operacion)
app.include_router(rutas_seguridad)
app.include_router(rutas_demo)


# ---------------------------------------------------------------- Páginas web (dashboard)
ESTATICOS = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return RedirectResponse("/centro")


@app.get("/favicon.ico", include_in_schema=False)
def icono():
    return FileResponse(ESTATICOS / "favicon.svg", media_type="image/svg+xml")


@app.get("/centro", include_in_schema=False)
def centro_de_mando():
    """Centro de mando de Protección Civil (pide iniciar sesión)."""
    return FileResponse(ESTATICOS / "centro.html")


@app.get("/ciudadano", include_in_schema=False)
def app_ciudadana():
    """Vista pública para celular."""
    return FileResponse(ESTATICOS / "ciudadano.html")


@app.get("/demo", include_in_schema=False)
def controles_demo():
    """Controles de la exposición (piden la contraseña de demo)."""
    return FileResponse(ESTATICOS / "demo.html")
