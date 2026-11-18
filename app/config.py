"""
config.py - Configuración de TermoMID.

Toda la configuración viene de VARIABLES DE ENTORNO. Así las contraseñas y llaves
nunca se escriben en el código ni se suben a GitHub:
- En local se leen del archivo .env (que está en .gitignore).
- En la nube (Render) se definen en el panel del servicio o las genera render.yaml.
"""
import os
import secrets

from dotenv import load_dotenv

# Carga el archivo .env si existe (solo en local; en la nube no hay .env).
load_dotenv()


def _secreto(nombre: str) -> str:
    """Lee un secreto de las variables de entorno.

    Si no existe, genera uno aleatorio para que la app NUNCA arranque con una
    contraseña conocida (por ejemplo "admin123"). Lo imprime en la consola para
    que en local se pueda usar, y avisa que conviene definirlo en .env.
    """
    valor = os.getenv(nombre)
    if not valor:
        valor = secrets.token_urlsafe(12)
        os.environ[nombre] = valor  # así otros módulos ven el mismo valor
        print(f"[config] AVISO: {nombre} no estaba definida; se generó una temporal: {valor}")
    return valor


def _url_base_datos() -> str:
    """Devuelve la URL de la base de datos.

    - Sin DATABASE_URL: SQLite local (un archivo termomid.db en la carpeta del proyecto).
    - Con DATABASE_URL de PostgreSQL (Render la entrega como postgres:// o postgresql://):
      la convertimos al formato que entiende SQLAlchemy con el driver psycopg 3.
    """
    url = os.getenv("DATABASE_URL", "sqlite:///./termomid.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# --- Base de datos ---
DATABASE_URL = _url_base_datos()

# --- Secretos (nunca en el código) ---
SECRET_KEY = _secreto("SECRET_KEY")                    # firma los tokens de sesión
SENSOR_MASTER_KEY = _secreto("SENSOR_MASTER_KEY")      # de aquí se deriva la llave única de cada sensor
OPERADOR_PASSWORD = _secreto("OPERADOR_PASSWORD")      # usuario "operador"
SUPERVISOR_PASSWORD = _secreto("SUPERVISOR_PASSWORD")  # usuario "supervisor"
DEMO_PASSWORD = _secreto("DEMO_PASSWORD")              # protege los controles de demostración

# --- Simulación ---
# Cuántos segundos reales dura un día simulado (240 s = 4 minutos).
DIA_SEGUNDOS = int(os.getenv("DIA_SEGUNDOS", "240"))
# Cada cuántos segundos reales se genera una ronda de lecturas y se recalcula todo.
INTERVALO_SEGUNDOS = int(os.getenv("INTERVALO_SEGUNDOS", "5"))
# Si es "true", el servidor genera sus propias lecturas (necesario en la nube).
# Si es "false", los datos deben llegar del script externo simulador.py.
SIMULADOR_INTERNO = os.getenv("SIMULADOR_INTERNO", "true").lower() == "true"
# Horas reales que se guardan las lecturas crudas antes de borrarlas (para no llenar la BD gratuita).
# Las alertas y los eventos de seguridad NO se borran nunca.
RETENCION_HORAS = float(os.getenv("RETENCION_HORAS", "6"))

# --- IP real detrás de un proxy (Render) ---
# En la nube, las peticiones llegan a través del balanceador de Render, que agrega la IP real
# del cliente al FINAL del encabezado X-Forwarded-For. Solo se confía en ese encabezado si
# CONFIAR_PROXY=true (en local no hay proxy y se usa la IP de la conexión).
CONFIAR_PROXY = os.getenv("CONFIAR_PROXY", "false").lower() == "true"
# Cuántas entradas, contando desde la derecha, agregan los proxies de confianza (normalmente 1).
PROXY_SALTOS = int(os.getenv("PROXY_SALTOS", "1"))

# Zona horaria en la que se MUESTRAN las fechas (en la base de datos todo se guarda en UTC).
ZONA_HORARIA = "America/Merida"
