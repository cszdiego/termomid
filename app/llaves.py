"""
llaves.py - Llave única por sensor (simula el certificado X.509 de cada dispositivo).

Cada llave se DERIVA de una llave maestra con HMAC-SHA256:
    llave(sensor) = HMAC(llave_maestra, id_del_sensor)
- Cada sensor tiene una llave distinta; si se roba una, no sirve para los demás sensores.
- La llave maestra solo la conocen el servidor y el gateway (variable SENSOR_MASTER_KEY).

En producción cada sensor tendría un certificado X.509 propio guardado en un chip
seguro y se autenticaría con TLS mutuo (mTLS) contra el gateway.
"""
import hashlib
import hmac


def llave_sensor(llave_maestra: str, sensor_id: str) -> str:
    return hmac.new(llave_maestra.encode(), sensor_id.encode(), hashlib.sha256).hexdigest()
