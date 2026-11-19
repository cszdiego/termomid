# TermoMID · Resumen técnico del Prototipo Alfa

Documento de referencia para redactar el reporte. Describe **exactamente** lo que hace el software.
Todo lo marcado como *simulado* no es un dato real. La sección 12 indica lo que **no** está en el
software y debe desarrollarse en el PDF.

- **App en la nube:** https://termomid.onrender.com
  - Centro de mando: https://termomid.onrender.com/centro
  - App ciudadana: https://termomid.onrender.com/ciudadano
  - Controles de demo: https://termomid.onrender.com/demo
  - API documentada: https://termomid.onrender.com/docs
- **Repositorio:** https://github.com/cszdiego/termomid
- **Capturas para el PDF:** carpeta [`docs/capturas/`](docs/capturas/)

---

## 1. Qué es

Prototipo de un sistema de alerta temprana por calor extremo **colonia por colonia** para Mérida, Yucatán.
Emula el sistema operativo (tipo ERP) de Protección Civil: catálogo de colonias, sensores, refugios y
padrón (como conteo), ingesta de telemetría, cálculo de riesgo, pronóstico y un flujo de alertas con
aprobación humana. Incluye ciberseguridad: bitácora de eventos y reglas de correlación tipo SIEM.

**Todos los datos son simulados:** población, porcentajes, padrón, coordenadas (aproximadas), sensores
y mediciones. No se usó ninguna fuente oficial dentro del software.

## 2. Arquitectura

```
 60 sensores (simulados) ──► Gateway LoRaWAN (simulado: simulador.py) ──HTTPS──┐
                                                                               ▼
 Simulador interno (tarea de fondo) ───────────────────────────────►  API FastAPI (Render, PaaS)
                                                                      │  ingesta + validación
                                                                      │  motor cada 5 s
                                                                      ▼
                                                               PostgreSQL 16 (Render)
                                                                      │
             ┌────────────────────────────┬───────────────────────────┤
             ▼                            ▼                           ▼
   Centro de mando (/centro)    App ciudadana (/ciudadano)    Controles de demo (/demo)
   operador / supervisor        pública, celular, ES/maya     protegidos con contraseña
```

| Capa | Tecnología |
|---|---|
| Backend | Python 3.13.11, FastAPI 0.115.12, SQLAlchemy 2.0.40, uvicorn 0.34.2 (un solo proceso) |
| Base de datos | PostgreSQL 16 en la nube; SQLite en local. La URL viene de `DATABASE_URL` |
| Frontend | HTML + CSS + JavaScript sin frameworks ni compilación; Leaflet 1.9.4 (mapa) y Chart.js 4.4.1 (gráfica), incluidos en el repositorio |
| Nube | Render (PaaS), plan gratuito, región Virginia (EE. UU.), definido en `render.yaml` (Blueprint) |
| Mapa base | Teselas de OpenStreetMap (única dependencia externa del navegador) |

**Organización del código (`app/`):** `config.py` (variables de entorno), `database.py` (conexión),
`modelos.py` (10 tablas), `catalogos.py` (datos maestros), `simulacion.py` (clima y sensores),
`calculos.py` (índice de calor, niveles, IRC), `pronostico.py`, `alertas.py` (flujo ERP),
`ingesta.py` (filtros de cada lectura), `seguridad.py` (bitácora, SIEM, login, roles),
`llaves.py` (llave por sensor), `motor.py` (tarea de fondo), `consultas.py` (KPIs),
`mensajes.py` (textos ES/maya), `rutas_*.py` (endpoints), `main.py` (arranque y encabezados de seguridad).

## 3. Datos maestros (simulados)

- **15 colonias:** Centro, García Ginerés, Itzimná, Montes de Amé, Francisco de Montejo, Ciudad Caucel,
  Las Américas, Chuburná, Pensiones, Emiliano Zapata Sur, San José Tecoh, Serapio Rendón,
  Castilla Cámara, Mulsay y Juan Pablo II. Cada una tiene coordenadas aproximadas, población,
  % de personas de 65+, % de hogares sin aire acondicionado, arbolado (0 a 1) y padrón de personas
  mayores que viven solas (**solo un número**).
- **Vulnerabilidad (0 a 1)** = 0.5 × mín(1, %65+ / 25 %) + 0.5 × %hogares sin A/C.
- **Capacidad (0 a 1)** = 0.7 × arbolado + 0.3 × mín(1, refugios en la colonia / 2).
- **60 sensores:** 2 por colonia (30) y los otros 30 repartidos **en proporción a la vulnerabilidad**
  (principio ético: más sensores donde hay más riesgo). Resultado: de 3 sensores (Montes de Amé,
  Las Américas) a 5 (Centro, Serapio Rendón). IDs `TMID-001` a `TMID-060`.
- **10 refugios climáticos** con capacidad de 50 a 150 personas, ocupación y estado abierto/cerrado.

## 4. Simulador de sensores

- **Reloj comprimido:** 1 día simulado = 240 s reales (`DIA_SEGUNDOS`); 10 s reales = 1 hora simulada.
- **Clima de temporada de calor (promedio de la ciudad):** temperatura de ~24.5 °C (madrugada) a
  ~35.5 °C (15:00); humedad de ~85 % (madrugada) a ~40 % (15:00); cada día varía ±1.2 °C.
- **Diferencia por colonia:** el sur es más caliente que el norte (~4 °C por cada 0.1° de latitud) y las
  colonias arboladas son más frescas. Diferencia total entre la más fresca y la más caliente: ~5.3 °C.
- **Cada lectura:** temperatura, humedad y temperatura de globo negro (temperatura + radiación solar).
- **Fallas aleatorias:** sensor caído (0.2 % por ronda, 20 a 90 s sin señal) y lecturas con ruido
  (3 %, ±1.5 °C).
- **Ola de calor (demo):** +5 °C durante 3 días simulados; sube y baja en rampa de ~30 s.
- **Dos formas de generar datos:** (1) tarea interna del servidor cada 5 s, para que en la nube haya datos
  siempre; (2) `simulador.py`, que actúa como gateway LoRaWAN y manda las lecturas por HTTPS con la llave
  de cada sensor.

## 5. Lógica de negocio

**Índice de calor (IC):** algoritmo del National Weather Service (fórmula simple de Steadman y, si da
80 °F o más, regresión de Rothfusz con sus dos ajustes por humedad). Verificado contra la tabla del NWS
con diferencias de 0.3 °C o menos.

**Niveles de alerta (por índice de calor):**

| Nivel | Índice de calor |
|---|---|
| Verde | menor a 32 °C |
| Amarillo | 32 °C a menos de 39 °C |
| Naranja | 39 °C a menos de 51 °C |
| Rojo | 51 °C o más |

**IRC (Índice de Riesgo por Colonia)** = Peligro × Vulnerabilidad × (1 − Capacidad), de 0 a 1.
Peligro = IC normalizado: 27 °C o menos = 0; 55 °C o más = 1. **El nivel de alerta lo define el IC;
el IRC ordena qué colonia atender primero.**

**Estado de cada colonia (cada 5 s):** promedio de la última lectura válida de cada sensor de la colonia
(excluye lecturas sospechosas y sensores en cuarentena).

**Pronóstico +24/+48/+72 h (máximo del día):**
pronóstico_colonia = pronóstico_regional + ajuste_colonia.
- Pronóstico regional: del modelo climático simulado (incluye la ola de calor si está programada).
- Ajuste de la colonia: diferencia promedio entre el IC de la colonia y el promedio de la ciudad en los
  últimos 60 ciclos.
- La gráfica muestra lo medido (último día simulado) y el pronóstico hora por hora a 72 h.
- **En producción** se usaría Kriging (interpolación espacial) y LightGBM (aprendizaje automático).

**KPIs del centro de mando:**
- Nivel de alerta de mañana: el nivel más alto entre los pronósticos +24 h de las colonias.
- Índice de calor máximo: el mayor IC actual entre colonias.
- Personas vulnerables expuestas: personas de 65+ en colonias que ahora están en naranja o rojo.
- Sensores activos: los que reportaron en los últimos 30 s, de 60.
- Refugios abiertos, de 10.
- Alertas por aprobar.

## 6. Flujo de alertas (tipo ERP): "el modelo propone, una persona aprueba"

```
PROPUESTA ──(1 firma)──► APROBADA ──► EMITIDA ──► CERRADA
     │
     └─ si es ROJA: PROPUESTA ─(firma 1)─► SEGUNDA_FIRMA ─(firma 2, OTRA persona)─► APROBADA
```

- El motor **propone** una alerta cuando una colonia llega a amarillo o más.
- **Máximo una alerta activa por colonia.** Si la colonia sube de nivel, se actualiza la misma alerta y
  vuelve a PROPUESTA: hay que aprobarla de nuevo (si sube a rojo, con doble firma). Si baja sin llegar a
  verde, conserva su nivel más alto.
- **Cierre automático:** la alerta se cierra si la colonia pasa 3 horas simuladas seguidas en verde.
- **Doble firma (rojo):** dos usuarios **distintos**. El sistema rechaza (HTTP 409) que la misma persona
  firme dos veces y que se emita sin las firmas.
- **Al emitir** (todo simulado, no se envía nada real):
  - Amarillo: avisos SMS (45 % de la población) y WhatsApp (35 %).
  - Naranja y rojo: además, llamadas a todo el padrón de la colonia y apertura del refugio con lugar más
    cercano; se estima que llega el 30 % del padrón (mínimo 5 personas).
- Los refugios se cierran al amanecer simulado (6:00).
- **App ciudadana:** solo muestra avisos **ya emitidos** por una persona; nunca alertas sin aprobar.

## 7. Usuarios y roles

| Usuario | Rol | Puede |
|---|---|---|
| `operador` | operador | Ver el tablero, aprobar y emitir alertas, abrir refugios, atender reportes |
| `supervisor` | supervisor | Todo lo anterior + ver la bitácora de seguridad y alertas SIEM + liberar sensores en cuarentena |

Las contraseñas **no están en el código**: en Render se generan al azar (`generateValue`) y se ven en el
panel del servicio, en *Environment*. El login entrega un token firmado con HMAC-SHA256 que dura 8 horas.
**En producción se agregaría MFA** (segundo factor) y contraseñas guardadas con hash (Argon2/bcrypt).

## 8. Ciberseguridad implementada

| Medida | Cómo funciona |
|---|---|
| Llave única por sensor | Simula el certificado X.509 del pitch. Llave = HMAC-SHA256(llave maestra, ID del sensor). Se exige en el encabezado `X-Sensor-Key`; si falta o es incorrecta: rechazo 401 y evento `LLAVE_INCORRECTA`. Un sensor que no está en el catálogo da `SENSOR_DESCONOCIDO` |
| Validación de rangos físicos | Temperatura 10 a 60 °C, humedad 0 a 100 %, globo negro 10 a 80 °C. Fuera de rango: rechazo 422 y evento `FUERA_DE_RANGO` |
| Detección de sensor manipulado | (A) salto de más de 8 °C respecto a su lectura anterior; (B) diferencia de más de 6 °C con la mediana de los otros sensores de su colonia (si reportan al menos 2). Solo compara contra lecturas de los últimos ~15 s. La lectura se guarda marcada como sospechosa, no se usa en los cálculos y genera `LECTURA_MANIPULADA` |
| Bitácora de seguridad | Tabla `eventos_seguridad`: fecha, IP, sensor, endpoint, tipo, motivo y severidad. Se conserva siempre |
| Reglas SIEM | Ver sección 9 |
| Mínimo privilegio | El operador recibe 403 al pedir la bitácora; el intento queda registrado (`ACCESO_DENEGADO`) |
| IP real detrás del proxy | Render agrega 3 entradas a `X-Forwarded-For` (cliente, Cloudflare, red interna). Se toma la 3.ª desde la derecha, así que un atacante **no puede falsificar su IP**. Verificado en la nube |
| Anti-XSS | Todo texto se escapa antes de mostrarse. Probado con un sensor llamado `FALSO-99<img src=x onerror=alert('XSS')>`, que aparece como texto (captura `seguridad-xss.png`) |
| Content-Security-Policy | Solo se ejecutan scripts del propio servidor (sin scripts en línea ni CDN); además `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` y `Referrer-Policy: no-referrer` |
| Secretos fuera del código | Variables de entorno; `.env` excluido de git; el repositorio se revisó antes de publicarse |
| Comparaciones en tiempo constante | Contraseñas, llaves y tokens se comparan con `hmac.compare_digest` |
| Reportes ciudadanos anónimos | Solo se guardan colonia y tipo. Límite: 5 por IP cada 10 min; si se excede, rechazo 429 y evento `ABUSO_REPORTES` |
| Privacidad | Ubicación solo a nivel colonia; el padrón es un número por colonia, sin nombre ni CURP |

## 9. Reglas SIEM implementadas

| | R1 · Posible intrusión (fuerza bruta) | R2 · Sensor manipulado |
|---|---|---|
| Condición (tipos de evento) | `LOGIN_FALLIDO`, `LLAVE_INCORRECTA`, `SENSOR_DESCONOCIDO`, `TOKEN_INVALIDO` | `LECTURA_MANIPULADA` |
| Agrupado por | IP de origen | ID del sensor |
| Umbral y ventana | 5 o más en 60 s | 3 o más en 300 s |
| Severidad | CRÍTICA | ALTA |
| Acción automática | Bloquear la IP 5 minutos (rechazo 429) y notificar | Poner el sensor en CUARENTENA (sus datos no se usan) y enviar cuadrilla |

Cada disparo crea un registro en `alertas_siem`. No se repite la misma alerta dentro de su ventana.
En la demo, los ataques usan IPs **reservadas para documentación** (203.0.113.66 y 203.0.113.77), así que
el bloqueo nunca cae sobre la computadora de quien presenta.

## 10. Endpoints principales

| Método | Ruta | Acceso |
|---|---|---|
| GET | `/api/salud`, `/api/estado`, `/api/mi-ip` | Público |
| POST | `/api/lecturas` | Llave del sensor (`X-Sensor-Key`) |
| GET | `/api/ciudadano/colonias`, `/api/ciudadano/{colonia}` | Público |
| POST | `/api/ciudadano/reportes` | Público, con límite anti-abuso |
| POST | `/api/login` | Público (cada fallo se registra) |
| GET | `/api/tablero`, `/api/kpis`, `/api/colonias`, `/api/colonias/{id}/curva`, `/api/alertas`, `/api/sensores`, `/api/refugios` | Operador o supervisor |
| POST | `/api/alertas/{id}/aprobar`, `/api/alertas/{id}/emitir`, `/api/refugios/{id}/abrir`, `/api/reportes/{id}/atender` | Operador o supervisor |
| GET | `/api/seguridad/eventos`, `/api/seguridad/siem`, `/api/seguridad/reglas` | Solo supervisor |
| POST | `/api/sensores/{id}/liberar` | Solo supervisor |
| POST | `/api/demo/ola-calor`, `tumbar-sensor`, `sensor-falso`, `sensor-manipulado`, `fuerza-bruta`, `restablecer` | Contraseña de demo (`X-Demo-Password`) |

## 11. Despliegue y operación

- **Render (PaaS), plan gratuito**, definido en `render.yaml`: servicio web + PostgreSQL 16, región
  Virginia, un solo proceso de uvicorn, health check en `/api/salud` y secretos generados por Render.
- **Retención:** las lecturas crudas y el historial de colonias se borran a las 6 h (`RETENCION_HORAS`).
  Las alertas, las firmas y los eventos de seguridad no se borran. Espacio estimado: 70 a 100 MB de 1 GB
  (medido en PostgreSQL: ~205 bytes por lectura con índices; ~259,000 lecturas en 6 h).
- **Fechas:** se guardan en UTC y se muestran en hora de Mérida (America/Merida, UTC−6).
- **Probado en:** PostgreSQL 16 y SQLite; Microsoft Edge en escritorio (1440 px) y celular (390 px);
  celular real con datos móviles contra la nube.

## 12. Limitaciones (decirlas con honestidad)

- Datos 100 % simulados; coordenadas aproximadas; los porcentajes por colonia son inventados.
- Pronóstico simplificado (regional simulado + ajuste promedio), no Kriging ni LightGBM.
- La llave HMAC por sensor **simula** el certificado X.509; no hay TLS mutuo ni chip seguro.
- Login sin MFA; contraseñas en variables de entorno, no con hash en base de datos.
- La bitácora vive en la misma base de datos; no se envía a un SIEM externo.
- El estado de la ola de calor y las IPs bloqueadas vive en memoria: se pierde si el servidor se reinicia.
- Plan gratuito: el servicio se duerme tras 15 min sin tráfico (tarda ~1 min en despertar) y la base
  **vence 30 días después de crearse** (a mediados de diciembre de 2026); después de 14 días de gracia se
  borra. Sin respaldos.
- Región en EE. UU. (Virginia), no en México.
- SMS, WhatsApp, llamadas y refugios son simulados; no se envía nada.
- Textos en maya: traducción breve de apoyo que debe validar un hablante nativo (p. ej., INDEMAYA).

## 13. Lo que NO está en el software y debe ir en el PDF

Estos temas los pide el parcial pero no son código. Abajo va lo que el software sí aporta para cada uno.

### 13.1 Diagrama IDMZ / Zero Trust
Dibujar zonas separadas:
- **Zona OT/campo:** sensores y gateways LoRaWAN.
- **IDMZ:** gateway en el borde (edge), que es lo único que habla con la nube.
- **Nube (PaaS):** API, base de datos y bitácora.
- **Zona corporativa:** operadores de Protección Civil.
- **Zona pública:** ciudadanos.

Principios Zero Trust ya reflejados en el prototipo: cada sensor se autentica con su propia llave (nunca
se confía por estar "dentro de la red"), cada petición del tablero lleva token y se valida el rol, mínimo
privilegio (operador frente a supervisor), validación de cada dato, la alerta roja exige dos personas y
todo queda registrado. En el diagrama, marcar como **propuesta de producción**: mTLS con certificados
X.509 por sensor, MFA, segmentación de red, Key Vault y envío de la bitácora a un SIEM.

### 13.2 Regla SIEM en formato técnico
Redactar R1 y R2 (sección 9) en un formato de SIEM real, por ejemplo Sigma o KQL de Microsoft Sentinel.
Ejemplo de R1 en pseudo-KQL (es una propuesta, no código del prototipo):
```
EventosSeguridad
| where Tipo in ("LOGIN_FALLIDO","LLAVE_INCORRECTA","SENSOR_DESCONOCIDO","TOKEN_INVALIDO")
| summarize Intentos = count() by IP, bin(Fecha, 60s)
| where Intentos >= 5
| extend Severidad = "Crítica", Accion = "Bloquear IP 5 min y notificar"
```

### 13.3 Justificación PaaS frente a IaaS
El prototipo corre en PaaS (Render), y para producción se propone Azure App Service + Azure Database for
PostgreSQL, que también es PaaS. Argumentos:
- No se administran servidores, parches ni sistema operativo.
- HTTPS, despliegue desde GitHub y health checks incluidos.
- Escalado gestionado.
- Menos personal técnico necesario en un gobierno municipal.

Con IaaS (máquinas virtuales) el equipo tendría que mantener el sistema operativo, el servidor web, los
respaldos y la seguridad del servidor. El README explica la migración a Azure (región México Central).

### 13.4 Barreras legacy y culturales
No están en el software. Temas a desarrollar:
- Integración con sistemas existentes (registros de salud, padrón del DIF, avisos actuales por municipio
  con estadística semanal).
- Formatos y datos en papel.
- Conectividad en colonias periféricas.
- Resistencia a cambiar alertas municipales por alertas por colonia.
- Confianza en un modelo; por eso se diseñó "el modelo propone, una persona aprueba".
- Brecha digital de personas mayores (por eso hay SMS y llamadas, no solo app).
- Lengua maya.
- Capacitación de operadores.

### 13.5 Valor conectado
Qué gana cada actor al conectar sensores, nube y personas:
- **Protección Civil:** ve el riesgo por colonia y con 24 a 72 h de anticipación, en lugar de estadística
  semanal por municipio.
- **Población:** recibe avisos específicos de su colonia, en su idioma, con su refugio más cercano.
- **Personas mayores que viven solas:** reciben una llamada.
- **Salud:** puede preparar la atención.
- **Datos para planeación urbana:** dónde faltan árboles o refugios (el IRC lo muestra).

El prototipo demuestra este flujo de punta a punta con datos simulados.

---
*Generado a partir del código del repositorio. Si el código cambia, actualizar este documento.*
