"""
simulador.py - Simula el GATEWAY LoRaWAN: manda las lecturas de los 60 sensores a la API por HTTPS.

Uso (con el entorno virtual activado):
    python simulador.py                                  # contra http://127.0.0.1:8000
    python simulador.py --url https://termomid.onrender.com
    python simulador.py --rondas 3                       # solo 3 rondas y termina

Necesita la variable SENSOR_MASTER_KEY (la misma que tiene el servidor) para calcular
la llave única de cada sensor. En local se lee del archivo .env.

Consejo: para probar SOLO este simulador, arranca el servidor con SIMULADOR_INTERNO=false.
"""
import argparse
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from app.llaves import llave_sensor
from app.simulacion import GeneradorLecturas

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Gateway LoRaWAN simulado de TermoMID")
    parser.add_argument("--url", default=os.getenv("API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--intervalo", type=float, default=5.0, help="segundos entre rondas")
    parser.add_argument("--rondas", type=int, default=0, help="0 = infinito")
    args = parser.parse_args()

    llave_maestra = os.getenv("SENSOR_MASTER_KEY")
    if not llave_maestra:
        sys.exit("Falta SENSOR_MASTER_KEY (ponla en .env o como variable de entorno).")

    url = args.url.rstrip("/")
    with httpx.Client(timeout=15) as cliente:
        # Preguntamos al servidor la duración del día simulado y si hay ola de calor
        estado = cliente.get(f"{url}/api/estado").json()
        generador = GeneradorLecturas(estado["dia_segundos"])
        llaves = {s["id"]: llave_sensor(llave_maestra, s["id"]) for s in generador.sensores}
        print(f"Gateway conectado a {url} | {len(llaves)} sensores | "
              f"1 día simulado = {estado['dia_segundos']} s")

        ronda = 0
        while args.rondas == 0 or ronda < args.rondas:
            ronda += 1
            estado = cliente.get(f"{url}/api/estado").json()
            lecturas = generador.generar(estado["extra_ola_hoy"])
            ok = rechazadas = sospechosas = 0
            for l in lecturas:
                r = cliente.post(f"{url}/api/lecturas", json=l,
                                 headers={"X-Sensor-Key": llaves[l["sensor_id"]]})
                if r.status_code == 201:
                    ok += 1
                    sospechosas += r.json()["sospechosa"]
                else:
                    rechazadas += 1
            print(f"Ronda {ronda} | hora simulada {estado['hora_simulada']:05.2f} | "
                  f"enviadas {len(lecturas)} | aceptadas {ok} | sospechosas {sospechosas} | "
                  f"rechazadas {rechazadas} | sensores caídos {60 - len(lecturas)}")
            time.sleep(args.intervalo)


if __name__ == "__main__":
    main()
