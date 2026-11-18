/* =====================================================================
   centro.js - Centro de mando de Protección Civil.

   Cómo funciona:
   1. Login -> el servidor devuelve un token firmado (se guarda en sessionStorage).
   2. Cada 4 segundos se pide /api/tablero (KPIs, colonias, alertas, refugios,
      sensores y reportes en UNA sola llamada) y se vuelve a dibujar todo,
      sin recargar la página.
   3. Los botones (aprobar, emitir, abrir refugio...) llaman a la API y
      luego se actualiza la vista.
   Seguridad: todo texto del servidor pasa por esc() (ver comun.js) antes de
   ponerse en la página, y no hay JavaScript en línea (lo prohíbe la CSP).
   ===================================================================== */
"use strict";

const INTERVALO_MS = 4000;

const estado = {
  usuario: null,          // {usuario, rol}
  datos: null,            // última respuesta de /api/tablero
  pestana: "mapa",
  horizonte: "ahora",     // ahora, +24h, +48h, +72h
  coloniaSel: null,       // colonia elegida para la gráfica
  alertaSel: null,        // alerta abierta en la pestaña Alertas
  vistas: new Set(),      // "id|actualizada" de alertas ya conocidas
  resaltadas: new Map(),  // id de alerta -> hasta cuándo se resalta
  siemVistas: new Set(),
  primeraCarga: true,
  temporizador: null,
  sinConexion: false,
  seguridadIntentada: false,
};

const $ = (id) => document.getElementById(id);

/* =====================================================================
   1. SESIÓN
   ===================================================================== */
async function iniciar() {
  $("form-login").addEventListener("submit", entrar);
  $("btn-salir").addEventListener("click", salir);
  if (leer("sessionStorage", "termomid_token")) {
    try {
      mostrarAplicacion(await api("/api/yo"));
      return;
    } catch (e) {
      borrar("sessionStorage", "termomid_token");
    }
  }
  $("pantalla-login").classList.remove("oculto");
}

async function entrar(evento) {
  evento.preventDefault();
  const error = $("login-error");
  error.classList.add("oculto");
  try {
    const r = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ usuario: $("login-usuario").value, password: $("login-password").value }),
    });
    guardar("sessionStorage", "termomid_token", r.token);
    $("login-password").value = "";
    mostrarAplicacion({ usuario: r.usuario, rol: r.rol });
  } catch (e) {
    error.textContent = e.message;  // textContent: nunca interpreta HTML
    error.classList.remove("oculto");
  }
}

function salir() {
  borrar("sessionStorage", "termomid_token");
  location.reload();
}

function mostrarAplicacion(usuario) {
  estado.usuario = usuario;
  $("pantalla-login").classList.add("oculto");
  $("aplicacion").classList.remove("oculto");
  $("avatar").textContent = usuario.usuario.slice(0, 2).toUpperCase();
  $("nombre-usuario").textContent = usuario.usuario;
  $("rol-usuario").textContent = "Rol: " + usuario.rol;
  $("rol-usuario").className = "etiqueta " + (usuario.rol === "supervisor" ? "rol-supervisor" : "etiqueta-marca");

  document.querySelectorAll(".pestana").forEach((b) =>
    b.addEventListener("click", () => cambiarPestana(b.dataset.pestana)));
  document.querySelectorAll("#selector-horizonte button").forEach((b) =>
    b.addEventListener("click", () => {
      estado.horizonte = b.dataset.h;
      document.querySelectorAll("#selector-horizonte button").forEach((x) => x.classList.toggle("activo", x === b));
      pintarMapa();
    }));
  ["capa-sensores", "capa-refugios", "capa-nombres"].forEach((id) => $(id).addEventListener("change", aplicarCapas));
  document.addEventListener("click", manejarClic);

  iniciarMapa();
  iniciarGrafica();
  setInterval(() => { $("reloj-real").textContent = fechaHoy(); }, 1000);
  $("reloj-real").textContent = fechaHoy();
  actualizar();
  estado.temporizador = setInterval(actualizar, INTERVALO_MS);
}

function cambiarPestana(nombre) {
  estado.pestana = nombre;
  document.querySelectorAll(".pestana").forEach((b) => b.classList.toggle("activa", b.dataset.pestana === nombre));
  document.querySelectorAll(".seccion").forEach((s) => s.classList.toggle("oculto", s.id !== "pestana-" + nombre));
  if (nombre === "mapa" && mapa) setTimeout(() => mapa.invalidateSize(), 50);
  if (nombre === "seguridad") actualizarSeguridad(true);
  if (estado.datos) dibujarTodo();
}

/* =====================================================================
   2. ACTUALIZACIÓN AUTOMÁTICA (cada 4 s)
   ===================================================================== */
async function actualizar() {
  try {
    estado.datos = await api("/api/tablero");
    if (estado.sinConexion) toast("Conexión recuperada", "Los datos vuelven a actualizarse.", "");
    estado.sinConexion = false;
    detectarNovedades();
    dibujarTodo();
    actualizarGrafica();
    if (estado.usuario.rol === "supervisor") actualizarSeguridad(false);
  } catch (e) {
    if (e.status === 401) { salir(); return; }
    if (!estado.sinConexion) toast("Sin conexión con el servidor", "Reintentando cada 4 segundos…", "ROJO");
    estado.sinConexion = true;
  }
}

/** Detecta alertas NUEVAS (o que acaban de escalar) para resaltarlas */
function detectarNovedades() {
  const nuevas = [];
  for (const a of estado.datos.alertas) {
    if (a.estado === "CERRADA") continue;
    const huella = a.id + "|" + a.actualizada;
    if (!estado.vistas.has(huella)) {
      estado.vistas.add(huella);
      if (!estado.primeraCarga && a.estado === "PROPUESTA") nuevas.push(a);
    }
  }
  const ahora = Date.now();
  nuevas.forEach((a) => estado.resaltadas.set(a.id, ahora + 20000));
  if (nuevas.length > 3) {
    toast(nuevas.length + " alertas nuevas propuestas", "Revisa el panel “Requieren acción”.", "NARANJA");
  } else {
    nuevas.forEach((a) => toast(
      (a.nivel_inicial !== a.nivel ? "Alerta escaló a " : "Nueva alerta propuesta: ") + NOMBRE_NIVEL[a.nivel],
      a.colonia + " · índice de calor " + a.indice_calor + " °C", a.nivel));
  }
  estado.primeraCarga = false;
}

function esNueva(id) {
  const hasta = estado.resaltadas.get(id);
  return hasta && hasta > Date.now();
}

function dibujarTodo() {
  dibujarKpis();
  pintarMapa();
  dibujarAcciones();
  dibujarTablaColonias();
  dibujarAlertas();
  dibujarRefugios();
  dibujarSensores();
}

/* =====================================================================
   3. KPIs
   ===================================================================== */
function dibujarKpis() {
  const k = estado.datos.kpis;
  $("reloj-sim").textContent = horaSimulada(k.hora_simulada) + (k.ola_calor ? " · ola de calor" : "");

  const caja = $("kpi-manana");
  caja.className = "tarjeta kpi kpi-destacado k-" + k.nivel_manana;
  $("kpi-manana-valor").textContent = NOMBRE_NIVEL[k.nivel_manana];
  $("kpi-manana-detalle").textContent = k.colonias_naranja_rojo_manana + " de " + k.colonias_total +
    " colonias en naranja o rojo · máx " + (k.indice_calor_max_manana ?? "—") + " °C";

  $("kpi-ic").textContent = k.indice_calor_max ?? "—";
  $("kpi-ic-detalle").textContent = k.colonia_mas_caliente ? k.colonia_mas_caliente : "sin datos";
  $("kpi-expuestas").textContent = num(k.personas_vulnerables_expuestas);
  $("kpi-expuestas-detalle").textContent = "65+ en naranja o rojo · " + num(k.padron_expuesto) + " en el padrón";
  $("kpi-sensores").textContent = k.sensores_activos;
  $("kpi-sensores-total").textContent = " / " + k.sensores_total;
  const sinSenal = k.sensores_total - k.sensores_activos - k.sensores_cuarentena;
  $("kpi-sensores-detalle").textContent = sinSenal + " sin señal · " + k.sensores_cuarentena + " en cuarentena";
  $("kpi-refugios").textContent = k.refugios_abiertos;
  $("kpi-refugios-total").textContent = " / " + estado.datos.refugios.length;
  $("kpi-pendientes").textContent = k.alertas_pendientes;
  $("kpi-pendientes-detalle").textContent = k.rojas_por_aprobar + " rojas (requieren doble firma)";

  const cuenta = $("cuenta-alertas");
  cuenta.textContent = k.alertas_pendientes;
  cuenta.classList.toggle("oculto", !k.alertas_pendientes);
}

/* =====================================================================
   4. MAPA (Leaflet) - celdas hexagonales agrupadas por colonia
   ===================================================================== */
let mapa = null;
const capas = {};
const celdas = [];               // {poligono, coloniaId}
const marcadoresSensor = {};
const marcadoresRefugio = {};

function iniciarMapa() {
  mapa = L.map("mapa", { preferCanvas: true, scrollWheelZoom: false }).setView([20.985, -89.645], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "© OpenStreetMap",
  }).addTo(mapa);
  capas.celdas = L.layerGroup().addTo(mapa);
  capas.nombres = L.layerGroup().addTo(mapa);
  capas.refugios = L.layerGroup().addTo(mapa);
  capas.sensores = L.layerGroup().addTo(mapa);
}

function aplicarCapas() {
  [["capa-sensores", "sensores"], ["capa-refugios", "refugios"], ["capa-nombres", "nombres"]].forEach(([id, capa]) => {
    if ($(id).checked) capas[capa].addTo(mapa); else capas[capa].remove();
  });
}

/** Crea una rejilla de hexágonos (~230 m) y asigna cada celda a la colonia más cercana. */
function construirCeldas(colonias) {
  const R = 250;  // radio del hexágono en metros
  const lat0 = 20.985, mLat = 111320, mLon = 111320 * Math.cos(lat0 * Math.PI / 180);
  const margen = 1600;
  const minLat = Math.min(...colonias.map((c) => c.lat)) - margen / mLat;
  const maxLat = Math.max(...colonias.map((c) => c.lat)) + margen / mLat;
  const minLon = Math.min(...colonias.map((c) => c.lon)) - margen / mLon;
  const maxLon = Math.max(...colonias.map((c) => c.lon)) + margen / mLon;
  const pasoX = 1.5 * R, pasoY = Math.sqrt(3) * R;
  const ancho = (maxLon - minLon) * mLon, alto = (maxLat - minLat) * mLat;

  for (let i = 0; i * pasoX <= ancho; i++) {
    for (let j = 0; j * pasoY <= alto; j++) {
      const x = i * pasoX, y = j * pasoY + (i % 2 ? pasoY / 2 : 0);
      const lat = minLat + y / mLat, lon = minLon + x / mLon;
      // La colonia "dueña" es la más cercana en proporción a su radio
      let mejor = null, razon = Infinity;
      for (const c of colonias) {
        const r = distanciaKm(lat, lon, c.lat, c.lon) * 1000 / c.radio_m;
        if (r < razon) { razon = r; mejor = c; }
      }
      if (razon > 1.75) continue;  // hasta 1.75 radios: así la ciudad se ve continua
      const vertices = [];
      for (let k = 0; k < 6; k++) {
        const ang = Math.PI / 3 * k;
        vertices.push([lat + (R * 0.97 * Math.sin(ang)) / mLat, lon + (R * 0.97 * Math.cos(ang)) / mLon]);
      }
      const poligono = L.polygon(vertices, { color: "#ffffff", weight: 1, fillOpacity: 0.78, fillColor: "#d5dadd" });
      poligono.on("click", (e) => abrirFichaColonia(mejor.id, e.latlng));
      poligono.addTo(capas.celdas);
      celdas.push({ poligono, coloniaId: mejor.id });
    }
  }
  // Nombres de colonia
  for (const c of colonias) {
    L.tooltip({ permanent: true, direction: "center", className: "etiqueta-colonia", interactive: false })
      .setLatLng([c.lat, c.lon]).setContent(esc(c.nombre)).addTo(capas.nombres);
  }
}

/** Nivel e índice de calor de una colonia según el horizonte elegido */
function valorHorizonte(c, h) {
  if (h === "ahora") return c.ahora ? { nivel: c.ahora.nivel, ic: c.ahora.indice_calor } : null;
  const p = c.pronostico && c.pronostico[h];
  return p ? { nivel: p.nivel, ic: p.indice_calor } : null;
}

function pintarMapa() {
  if (!estado.datos || !mapa) return;
  const { colonias, sensores, refugios } = estado.datos;
  if (!celdas.length) construirCeldas(colonias);
  const porId = Object.fromEntries(colonias.map((c) => [c.id, c]));

  for (const celda of celdas) {
    const v = valorHorizonte(porId[celda.coloniaId], estado.horizonte);
    celda.poligono.setStyle({ fillColor: v ? COLOR_MAPA[v.nivel] : "#d5dadd" });
  }

  for (const s of sensores) {
    const estilo = s.estado === "ACTIVO" ? { color: "#ffffff", fillColor: "#1c2a32", radius: 3.5, weight: 1, fillOpacity: 1 }
      : s.estado === "CUARENTENA" ? { color: "#6b3fa0", fillColor: "#ffffff", radius: 5.5, weight: 3, fillOpacity: 1 }
      : { color: "#7d8a91", fillColor: "#ffffff", radius: 3.5, weight: 1.5, fillOpacity: 1 };
    let m = marcadoresSensor[s.id];
    if (!m) {
      m = marcadoresSensor[s.id] = L.circleMarker([s.lat, s.lon], estilo).addTo(capas.sensores);
    }
    m.setStyle(estilo);
    ponerTooltip(m, esc(s.id) + " · " + esc(textoEstadoSensor(s.estado)));
  }

  for (const r of refugios) {
    const icono = L.divIcon({ className: "icono-refugio" + (r.abierto ? "" : " cerrado"), iconSize: [12, 12] });
    let m = marcadoresRefugio[r.id];
    if (!m) m = marcadoresRefugio[r.id] = L.marker([r.lat, r.lon], { icon: icono, keyboard: false }).addTo(capas.refugios);
    else m.setIcon(icono);
    ponerTooltip(m, esc(r.nombre) + " · " + (r.abierto ? "abierto" : "cerrado") + " · " + r.ocupacion + "/" + r.capacidad);
  }
}

/** Leaflet trata el texto del tooltip como HTML, por eso siempre se le pasa ya escapado */
function ponerTooltip(capa, html) {
  if (capa.getTooltip()) capa.setTooltipContent(html); else capa.bindTooltip(html);
}

function refugioCercano(c) {
  let mejor = null, dist = Infinity;
  for (const r of estado.datos.refugios) {
    const d = distanciaKm(c.lat, c.lon, r.lat, r.lon);
    if (d < dist) { dist = d; mejor = r; }
  }
  return mejor ? { refugio: mejor, km: dist } : null;
}

/** Ficha emergente al tocar una colonia en el mapa (como en el wireframe) */
function abrirFichaColonia(coloniaId, latlng) {
  const c = estado.datos.colonias.find((x) => x.id === coloniaId);
  const h = estado.horizonte;
  const v = valorHorizonte(c, h);
  const cerca = refugioCercano(c);
  const activa = estado.datos.alertas.find((a) => a.colonia_id === c.id && a.estado !== "CERRADA");
  const html = `
    <div class="popup-colonia">
      <h3>${esc(c.nombre)}</h3>
      <dl>
        <dt>Nivel ${esc(h === "ahora" ? "ahora" : h)}</dt><dd>${v ? chipNivel(v.nivel) : "sin datos"}</dd>
        <dt>Índice de calor</dt><dd>${v ? esc(v.ic) + " °C" : "—"}</dd>
        <dt>IRC ahora</dt><dd>${c.ahora ? esc(c.ahora.irc.toFixed(2)) : "—"}</dd>
        <dt>Personas 65+</dt><dd>${num(c.mayores_65)}</dd>
        <dt>Padrón (viven solos)</dt><dd>${num(c.padron_mayores_solos)}</dd>
        <dt>Refugio más cercano</dt><dd>${cerca ? esc(cerca.km.toFixed(1)) + " km" : "—"}</dd>
      </dl>
      <button class="btn btn-marca" data-accion="ver-colonia" data-id="${esc(c.id)}">Ver pronóstico</button>
      ${activa ? `<button class="btn" data-accion="ver-alerta" data-id="${esc(activa.id)}">Ver alerta</button>` : ""}
    </div>`;
  L.popup({ maxWidth: 280 }).setLatLng(latlng).setContent(html).openOn(mapa);
}

/* =====================================================================
   5. PANEL "REQUIEREN ACCIÓN"
   ===================================================================== */
function dibujarAcciones() {
  const { alertas, sensores, reportes } = estado.datos;
  const yo = estado.usuario.usuario;
  const tarjetas = [];

  const pendientes = alertas.filter((a) => ["PROPUESTA", "SEGUNDA_FIRMA", "APROBADA"].includes(a.estado))
    .sort((a, b) => ORDEN_NIVEL[b.nivel] - ORDEN_NIVEL[a.nivel] || b.irc - a.irc);

  for (const a of pendientes) {
    const yaFirme = a.firmas.some((f) => f.usuario === yo);
    let titulo, botones = "";
    if (a.estado === "APROBADA") {
      titulo = "Lista para emitir · alerta " + ALERTA_NIVEL[a.nivel];
      botones = `<button class="btn btn-marca" data-accion="emitir" data-id="${a.id}">Emitir aviso</button>`;
    } else {
      titulo = (a.estado === "SEGUNDA_FIRMA" ? "Falta 2.ª firma · alerta roja" : "Propuesta de alerta " + ALERTA_NIVEL[a.nivel]);
      botones = `<button class="btn ${a.nivel === "ROJO" ? "btn-rojo" : "btn-marca"}" data-accion="aprobar" data-id="${a.id}" ${yaFirme ? "disabled" : ""}>
        ${a.estado === "SEGUNDA_FIRMA" ? "Dar 2.ª firma" : "Aprobar"}</button>`;
    }
    const ref = a.alcance.refugio;
    if (ref && !ref.abierto) botones += `<button class="btn" data-accion="abrir-refugio" data-id="${ref.id}">Abrir refugio</button>`;
    botones += `<button class="btn" data-accion="ver-alerta" data-id="${a.id}">Ver detalle</button>`;
    const firmasTxt = a.estado === "APROBADA" ? "Aprobada por " + a.firmas.map((f) => f.usuario).join(" y ")
      : a.firmas.length + " de " + a.firmas_requeridas + " firma(s)" + (yaFirme ? " · ya firmaste; falta otra persona" : "");
    tarjetas.push(`
      <div class="accion l-${a.nivel} ${esNueva(a.id) ? "nueva" : ""}">
        <div class="accion-cabeza"><b>${esc(titulo)} · ${esc(a.colonia)}${esNueva(a.id) ? '<span class="insignia-nueva">NUEVA</span>' : ""}</b>
          <span class="hace">${esc(hace(a.actualizada))}</span></div>
        <p>${esc(a.motivo)}<br><b>${esc(firmasTxt)}</b></p>
        <div class="botones">${botones}</div>
      </div>`);
  }

  const cuarentena = sensores.filter((s) => s.estado === "CUARENTENA");
  for (const s of cuarentena) {
    tarjetas.push(`
      <div class="accion l-morado">
        <div class="accion-cabeza"><b>Sensor en cuarentena · ${esc(s.id)}</b></div>
        <p>Posible manipulación (regla SIEM R2). Sus lecturas no se usan en los cálculos. Enviar cuadrilla a revisarlo.</p>
        ${estado.usuario.rol === "supervisor" ? `<div class="botones"><button class="btn" data-accion="liberar" data-id="${esc(s.id)}">Liberar sensor</button></div>` : '<p class="chico suave">Solo el supervisor puede liberarlo.</p>'}
      </div>`);
  }

  const sinSenal = sensores.filter((s) => s.estado === "SIN_SENAL");
  if (sinSenal.length) {
    tarjetas.push(`
      <div class="accion l-gris">
        <div class="accion-cabeza"><b>${sinSenal.length} sensor(es) sin reportar</b></div>
        <p>${esc(sinSenal.slice(0, 8).map((s) => s.id).join(", "))}${sinSenal.length > 8 ? "…" : ""} · revisar batería o señal LoRa.</p>
      </div>`);
  }

  for (const r of reportes) {
    tarjetas.push(`
      <div class="accion l-gris">
        <div class="accion-cabeza"><b>Reporte ciudadano · ${esc(r.colonia)}</b><span class="hace">${esc(hace(r.fecha))}</span></div>
        <p>${esc(r.descripcion)} (anónimo)</p>
        <div class="botones"><button class="btn" data-accion="atender" data-id="${r.id}">Marcar atendido</button></div>
      </div>`);
  }

  $("acciones").innerHTML = tarjetas.length ? tarjetas.join("") : '<div class="vacio">Nada pendiente. Las alertas aparecerán aquí cuando el modelo las proponga.</div>';
  $("cuenta-acciones").textContent = tarjetas.length + " abiertas";
}

/* =====================================================================
   6. TABLA DE COLONIAS (ordenada por IRC de mañana)
   ===================================================================== */
function ircManana(c) {
  const p = c.pronostico["+24h"];
  const peligro = Math.min(1, Math.max(0, (p.indice_calor - 27) / 28));
  return peligro * c.vulnerabilidad * (1 - c.capacidad);
}

const ACCION_SUGERIDA = {
  ROJO: ["Alerta roja", "etiqueta-rojo"], NARANJA: ["Abrir refugio", "etiqueta-naranja"],
  AMARILLO: ["Aviso preventivo", "etiqueta-amarillo"], VERDE: ["Vigilancia", "etiqueta-verde"],
};

function dibujarTablaColonias() {
  const filas = estado.datos.colonias.map((c) => ({ c, irc: ircManana(c) })).sort((a, b) => b.irc - a.irc);
  if (!estado.coloniaSel && filas.length) estado.coloniaSel = filas[0].c.id;
  $("tabla-colonias").innerHTML = filas.map(({ c, irc }) => {
    const p = c.pronostico["+24h"];
    const cerca = refugioCercano(c);
    const [accion, clase] = ACCION_SUGERIDA[p.nivel];
    const color = COLOR_FUERTE[nivelDe(p.indice_calor)];
    return `<tr class="fila-clic ${c.id === estado.coloniaSel ? "seleccionada" : ""}" data-accion="ver-colonia" data-id="${esc(c.id)}">
      <td><b>${esc(c.nombre)}</b></td>
      <td>${c.ahora ? chipNivel(c.ahora.nivel) : '<span class="suave">sin datos</span>'}</td>
      <td class="num"><span class="barra"><i data-ancho="${Math.round(irc * 250)}" data-color="${color}"></i></span>${irc.toFixed(2)}</td>
      <td class="num">${esc(p.indice_calor)} °C ${chipNivel(p.nivel)}</td>
      <td class="num">${num(c.mayores_65)}</td>
      <td class="num">${cerca ? esc(cerca.km.toFixed(1)) + " km" : "—"}</td>
      <td><span class="etiqueta ${clase}">${esc(accion)}</span></td>
    </tr>`;
  }).join("");
  aplicarAnchos($("tabla-colonias"));
}

/** Los anchos y colores de las barras se ponen con JavaScript (la CSP no permite style="" en el HTML). */
function aplicarAnchos(raiz) {
  raiz.querySelectorAll("[data-ancho]").forEach((el) => {
    el.style.width = Math.min(100, Math.max(2, Number(el.dataset.ancho))) + "%";
    if (el.dataset.color) el.style.background = el.dataset.color;
  });
}

/* =====================================================================
   7. GRÁFICA (Chart.js): medido + pronóstico 72 h, con bandas de nivel
   ===================================================================== */
let grafica = null;

const pluginBandas = {
  id: "bandas",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { x, y } } = chart;
    const bandas = [[-100, 32, "rgba(143,207,174,.22)"], [32, 39, "rgba(242,201,76,.22)"],
                    [39, 51, "rgba(240,146,74,.18)"], [51, 200, "rgba(224,90,90,.16)"]];
    ctx.save();
    for (const [desde, hasta, color] of bandas) {
      const top = Math.max(a.top, y.getPixelForValue(hasta));
      const bottom = Math.min(a.bottom, y.getPixelForValue(desde));
      if (bottom > top) { ctx.fillStyle = color; ctx.fillRect(a.left, top, a.right - a.left, bottom - top); }
    }
    // Línea vertical de "ahora"
    const x0 = x.getPixelForValue(0);
    ctx.strokeStyle = "#1c2a32"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, a.top); ctx.lineTo(x0, a.bottom); ctx.stroke();
    ctx.restore();
  },
};

function iniciarGrafica() {
  grafica = new Chart($("grafica"), {
    type: "line",
    data: { datasets: [
      { label: "Medido (sensores)", data: [], borderColor: "#1c2a32", borderWidth: 2, pointRadius: 0, tension: 0.25 },
      { label: "Pronóstico", data: [], borderColor: "#134f5c", borderDash: [6, 4], borderWidth: 2, tension: 0.3,
        pointRadius: (ctx) => (ctx.raw && ctx.raw.maximo ? 5 : 0), pointBackgroundColor: "#d43c3c" },
    ] },
    options: {
      animation: false, maintainAspectRatio: false, parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: { type: "linear", min: -24, max: 72,
             ticks: { stepSize: 12, callback: (v) => (v === 0 ? "ahora" : (v > 0 ? "+" : "") + v + " h") },
             grid: { color: "rgba(0,0,0,.05)" } },
        y: { suggestedMin: 22, suggestedMax: 56, ticks: { callback: (v) => v + "°" }, grid: { color: "rgba(0,0,0,.05)" } },
      },
      plugins: {
        legend: { position: "top", align: "end", labels: { boxWidth: 18, font: { size: 11 } } },
        tooltip: { callbacks: {
          title: (items) => { const h = items[0].raw.x; return h === 0 ? "ahora" : (h > 0 ? "+" : "") + h.toFixed(1) + " h simuladas"; },
          label: (item) => item.dataset.label + ": " + item.raw.y + " °C (" + NOMBRE_NIVEL[nivelDe(item.raw.y)] + ")",
        } },
      },
    },
    plugins: [pluginBandas],
  });
}

async function actualizarGrafica() {
  if (!estado.coloniaSel) return;
  try {
    const curva = await api("/api/colonias/" + encodeURIComponent(estado.coloniaSel) + "/curva");
    const pron = curva.pronostico.map((p) => ({ x: p.h, y: p.indice_calor }));
    let iMax = 0;
    pron.forEach((p, i) => { if (p.y > pron[iMax].y) iMax = i; });
    if (pron.length) pron[iMax].maximo = true;
    grafica.data.datasets[0].data = curva.medido.map((p) => ({ x: p.h, y: p.indice_calor }));
    grafica.data.datasets[1].data = pron;
    grafica.update();
    const c = estado.datos.colonias.find((x) => x.id === estado.coloniaSel);
    $("titulo-grafica").textContent = "Pronóstico 72 h · " + (c ? c.nombre : "");
  } catch (e) { /* se reintenta en el siguiente ciclo */ }
}

/* =====================================================================
   8. ALERTAS: lista + detalle con doble firma (diapositiva 9)
   ===================================================================== */
const TEXTO_ESTADO = {
  PROPUESTA: ["Propuesta del modelo", "etiqueta-amarillo"], SEGUNDA_FIRMA: ["Falta 2.ª firma", "etiqueta-rojo"],
  APROBADA: ["Aprobada · por emitir", "etiqueta-marca"], EMITIDA: ["Emitida", "etiqueta-verde"], CERRADA: ["Cerrada", ""],
};

function dibujarAlertas() {
  const alertas = estado.datos.alertas;
  $("lista-alertas").innerHTML = alertas.length ? alertas.map((a) => {
    const [txt, clase] = TEXTO_ESTADO[a.estado];
    return `<button class="item-alerta l-${a.estado === "CERRADA" ? "gris" : a.nivel} ${a.id === estado.alertaSel ? "sel" : ""} ${esNueva(a.id) ? "nueva" : ""}"
                    data-accion="ver-alerta" data-id="${a.id}">
      <div class="fila">${chipNivel(a.nivel)}<b>${esc(a.colonia)}</b>${esNueva(a.id) ? '<span class="insignia-nueva">NUEVA</span>' : ""}<span class="hace">${esc(hace(a.actualizada))}</span></div>
      <div class="fila chico"><span class="etiqueta ${clase}">${esc(txt)}</span>
        <span class="suave">IC ${esc(a.indice_calor)} °C · ${a.firmas.length}/${a.firmas_requeridas} firma(s)</span></div>
    </button>`;
  }).join("") : '<div class="vacio">Todavía no hay alertas.</div>';

  if (!estado.alertaSel) {
    const primera = alertas.find((a) => a.estado !== "CERRADA");
    if (primera) estado.alertaSel = primera.id;
  }
  dibujarDetalleAlerta();
}

function dibujarDetalleAlerta() {
  const a = estado.datos.alertas.find((x) => x.id === estado.alertaSel);
  const caja = $("detalle-alerta");
  if (!a) { caja.innerHTML = '<div class="vacio">Elige una alerta de la lista.</div>'; return; }
  const yo = estado.usuario.usuario;
  const yaFirme = a.firmas.some((f) => f.usuario === yo);
  const [txtEstado, claseEstado] = TEXTO_ESTADO[a.estado];
  const ex = a.explicacion, al = a.alcance;
  const naranjaOMas = ORDEN_NIVEL[a.nivel] >= 2;
  const irc = ex.peligro * ex.vulnerabilidad * ex.falta_capacidad;

  // --- Firmas: una fila por firma requerida
  const filasFirma = [];
  for (let i = 0; i < a.firmas_requeridas; i++) {
    const f = a.firmas[i];
    if (f) {
      filasFirma.push(`<div class="firma"><span class="ic ok" aria-hidden="true">✓</span>
        <span><b>${esc(f.usuario)}</b> (${esc(f.rol)}) · ${i === 0 ? "revisó y aprobó" : "segunda firma"}</span><span class="hora">${esc(horaCorta(f.fecha))}</span></div>`);
    } else {
      const quien = i === 0 ? "Un operador o supervisor" : "Otra persona distinta a " + (a.firmas[0] ? a.firmas[0].usuario : "la primera");
      filasFirma.push(`<div class="firma"><span class="ic pend">${i + 1}</span><span>${esc(quien)} · <b>pendiente</b></span></div>`);
    }
  }

  // --- Botones según el estado
  let botones = "", nota = "";
  if (a.estado === "PROPUESTA" || a.estado === "SEGUNDA_FIRMA") {
    const n = a.firmas.length + 1;
    botones = `<button class="btn ${a.nivel === "ROJO" ? "btn-rojo" : "btn-marca"} btn-grande" data-accion="aprobar" data-id="${a.id}" ${yaFirme ? "disabled" : ""}>
      Aprobar · firma ${n} de ${a.firmas_requeridas}</button>`;
    nota = yaFirme ? `Ya firmaste como <b>${esc(yo)}</b>. Falta la firma de <b>otra persona</b> (inicia sesión con el otro usuario en otra ventana o dispositivo).`
      : (a.nivel === "ROJO" ? "La alerta roja exige dos firmas de personas distintas antes de emitirse." : "Una firma basta para este nivel.");
  } else if (a.estado === "APROBADA") {
    botones = `<button class="btn btn-marca btn-grande" data-accion="emitir" data-id="${a.id}">Emitir aviso a la colonia</button>`;
    nota = "Al emitir se envían los avisos (simulados)" + (naranjaOMas ? ", se programan las llamadas al padrón y se abre el refugio." : ".");
  } else if (a.estado === "EMITIDA") {
    const ref = a.refugio_id && al.refugio ? al.refugio.nombre : null;
    botones = `<div class="resultado-emision"><b>Aviso emitido</b> por ${esc(a.emitida_por)} a las ${esc(horaCorta(a.emitida_en))}<br>
      ${num(a.avisos_sms)} SMS · ${num(a.avisos_whatsapp)} WhatsApp · ${num(a.personas_a_llamar)} llamadas al padrón${ref ? " · refugio abierto: " + esc(ref) : ""}</div>`;
  } else {
    nota = "Alerta cerrada " + esc(horaCorta(a.cerrada_en)) + ".";
  }
  if (al.refugio && !al.refugio.abierto && a.estado !== "CERRADA") {
    botones += `<button class="btn" data-accion="abrir-refugio" data-id="${al.refugio.id}">Abrir refugio: ${esc(al.refugio.nombre)}</button>`;
  }

  const anteriores = a.historial_firmas.filter((f) => f.nivel !== a.nivel);

  caja.innerHTML = `
    <div class="tarjeta-titulo">
      <h2>Alerta temprana · ${esc(a.colonia)}</h2><span class="etiqueta ${claseEstado}">${esc(txtEstado)}</span>
      ${esNueva(a.id) ? '<span class="insignia-nueva">NUEVA</span>' : ""}
    </div>
    <div class="detalle-alerta">
      <div class="pasos">
        <div class="paso"><span class="n">1</span><div><h4>Zona</h4><div class="chips"><span class="chip">${esc(a.colonia)}</span></div></div></div>
        <div class="paso"><span class="n">2</span><div><h4>Nivel</h4>
          <div class="niveles3">
            ${["AMARILLO", "NARANJA", "ROJO"].map((n) => `<div class="${n === a.nivel ? "marcado m-" + n : ""}"><i class="c-${n}"></i>${esc(NOMBRE_NIVEL[n])}${n === "ROJO" ? " · peligro extremo" : ""}</div>`).join("")}
          </div></div></div>
        <div class="paso"><span class="n">3</span><div><h4>Situación</h4>
          <div class="datos-par">
            <div><span>Índice de calor (máx.)</span><b>${esc(a.indice_calor)} °C</b></div>
            <div><span>IRC</span><b>${esc(a.irc.toFixed(2))}</b></div>
            <div><span>Propuesta</span><b>${esc(horaCorta(a.creada))}</b></div>
            <div><span>Última actualización</span><b>${esc(horaCorta(a.actualizada))}</b></div>
          </div>
          ${a.nivel_inicial !== a.nivel ? `<p class="chico suave">Escaló de ${chipNivel(a.nivel_inicial)} a ${chipNivel(a.nivel)}: se pidió nueva aprobación.</p>` : ""}
        </div></div>
        <div class="paso"><span class="n">4</span><div><h4>Canales</h4><div class="chips">
          <span class="chip">✓ SMS</span><span class="chip">✓ WhatsApp</span><span class="chip">✓ Notificación de la app</span>
          <span class="chip ${naranjaOMas ? "" : "no"}">${naranjaOMas ? "✓ " : ""}Llamadas al padrón</span>
          <span class="chip ${naranjaOMas ? "" : "no"}">${naranjaOMas ? "✓ " : ""}Abrir refugio</span></div></div></div>
        <div class="paso"><span class="n">5</span><div><h4>Idioma</h4><div class="chips"><span class="chip">✓ Español</span><span class="chip">✓ Maya</span></div></div></div>
        <div class="paso"><span class="n">6</span><div><h4>Alcance estimado</h4>
          <div class="alcance">
            <div><div class="t">Personas en la zona</div><div class="v num">${num(al.poblacion)}</div></div>
            <div><div class="t">65 años o más</div><div class="v num">${num(al.mayores_65)}</div></div>
            <div><div class="t">Padrón con llamada</div><div class="v num">${num(al.padron_llamadas)}</div></div>
            <div><div class="t">Refugio a activar</div><div class="v chico">${al.refugio && naranjaOMas ? esc(al.refugio.nombre) + " · " + esc(al.refugio.distancia_km) + " km" : "—"}</div></div>
          </div></div></div>
      </div>

      <div class="pasos">
        <div>
          <h4>Vista previa del mensaje <span class="suave chico">· SMS / WhatsApp (simulado)</span></h4>
          <div class="caja-mensaje"><div class="tit">${esc(a.mensaje.titulo)}</div>${esc(a.mensaje.es)}</div>
          <div class="caja-maya"><b>Maya:</b> ${esc(a.mensaje.maya)}<br><span class="suave chico">Traducción de apoyo: debe validarla un hablante nativo (p. ej. INDEMAYA) antes del envío.</span></div>
        </div>
        <div>
          <h4>¿Por qué el modelo propone alerta ${esc(ALERTA_NIVEL[a.nivel])}?</h4>
          <div class="factores">
            <span>Peligro (índice de calor)</span><span class="b"><i data-ancho="${ex.peligro * 100}" data-color="#d43c3c"></i></span><span class="val">${ex.peligro.toFixed(2)}</span>
            <span>Vulnerabilidad</span><span class="b"><i data-ancho="${ex.vulnerabilidad * 100}" data-color="#e8711a"></i></span><span class="val">${ex.vulnerabilidad.toFixed(2)}</span>
            <span>Falta de capacidad (1 − C)</span><span class="b"><i data-ancho="${ex.falta_capacidad * 100}" data-color="#e2b100"></i></span><span class="val">${ex.falta_capacidad.toFixed(2)}</span>
          </div>
          <p class="chico suave">IRC = P × V × (1 − C) = ${ex.peligro.toFixed(2)} × ${ex.vulnerabilidad.toFixed(2)} × ${ex.falta_capacidad.toFixed(2)} = <b>${irc.toFixed(2)}</b>.
            Vulnerabilidad: ${Math.round(ex.pct_mayores * 100)} % de 65+ y ${Math.round(ex.pct_sin_ac * 100)} % de hogares sin aire. Arbolado: ${Math.round(ex.arbolado * 100)} %.
            El nivel lo decide el índice de calor; el IRC ordena qué colonia atender primero.</p>
        </div>
        <div>
          <h4>${a.firmas_requeridas === 2 ? "Doble aprobación para alertas rojas" : "Aprobación humana"}</h4>
          <div class="firmas">${filasFirma.join("")}</div>
          ${anteriores.length ? `<p class="chico suave">Firmas de niveles anteriores: ${anteriores.map((f) => esc(f.usuario) + " (" + esc(NOMBRE_NIVEL[f.nivel]) + ")").join(", ")}</p>` : ""}
          <div class="pasos">${botones}</div>
          ${nota ? `<p class="aviso-regla">${nota}</p>` : ""}
        </div>
      </div>
    </div>`;
  aplicarAnchos(caja);
}

/* =====================================================================
   9. REFUGIOS Y SENSORES
   ===================================================================== */
function dibujarRefugios() {
  const nombres = Object.fromEntries(estado.datos.colonias.map((c) => [c.id, c.nombre]));
  $("lista-refugios").innerHTML = estado.datos.refugios.map((r) => `
    <div class="tarjeta refugio">
      <div class="fila"><b>${esc(r.nombre)}</b><span class="etiqueta ${r.abierto ? "etiqueta-verde" : ""}">${r.abierto ? "Abierto" : "Cerrado"}</span></div>
      <div class="fila suave"><span>${esc(nombres[r.colonia_id] || r.colonia_id)}</span><span class="num">${r.ocupacion} / ${r.capacidad} personas</span></div>
      <div class="fila"><span class="barra ancha"><i data-ancho="${r.ocupacion / r.capacidad * 100}" data-color="#2563c9"></i></span></div>
      ${r.abierto ? "" : `<div class="fila"><button class="btn" data-accion="abrir-refugio" data-id="${r.id}">Abrir refugio</button></div>`}
    </div>`).join("");
  aplicarAnchos($("lista-refugios"));
}

function textoEstadoSensor(e) {
  return { ACTIVO: "Activo", SIN_SENAL: "Sin señal", CUARENTENA: "Cuarentena" }[e] || e;
}

function dibujarSensores() {
  const sensores = estado.datos.sensores;
  const nombres = Object.fromEntries(estado.datos.colonias.map((c) => [c.id, c.nombre]));
  const cuenta = (e) => sensores.filter((s) => s.estado === e).length;
  $("resumen-sensores").innerHTML = `<span class="etiqueta etiqueta-verde">${cuenta("ACTIVO")} activos</span>
    <span class="etiqueta">${cuenta("SIN_SENAL")} sin señal</span><span class="etiqueta etiqueta-morado">${cuenta("CUARENTENA")} en cuarentena</span>`;
  const supervisor = estado.usuario.rol === "supervisor";
  const orden = { CUARENTENA: 0, SIN_SENAL: 1, ACTIVO: 2 };
  $("tabla-sensores").innerHTML = [...sensores].sort((a, b) => orden[a.estado] - orden[b.estado] || a.id.localeCompare(b.id)).map((s) => `
    <tr><td class="codigo">${esc(s.id)}</td><td>${esc(nombres[s.colonia_id])}</td>
      <td><span class="etiqueta ${s.estado === "ACTIVO" ? "etiqueta-verde" : s.estado === "CUARENTENA" ? "etiqueta-morado" : ""}">${esc(textoEstadoSensor(s.estado))}</span></td>
      <td class="num">${esc(hora(s.ultima_lectura))}</td>
      <td>${s.estado === "CUARENTENA" && supervisor ? `<button class="btn" data-accion="liberar" data-id="${esc(s.id)}">Liberar</button>` : ""}</td></tr>`).join("");
}

/* =====================================================================
   10. SEGURIDAD (solo supervisor; el operador recibe 403 y queda registrado)
   ===================================================================== */
async function actualizarSeguridad(alAbrir) {
  if (estado.usuario.rol !== "supervisor") {
    $("seguridad-sin-permiso").classList.remove("oculto");
    $("seguridad-contenido").classList.add("oculto");
    if (alAbrir) { try { await api("/api/seguridad/eventos"); } catch (e) { /* 403 esperado: queda en la bitácora */ } }
    return;
  }
  try {
    const [eventos, siem] = await Promise.all([api("/api/seguridad/eventos?limite=60"), api("/api/seguridad/siem")]);
    if (!estado.reglas) estado.reglas = await api("/api/seguridad/reglas");
    dibujarSeguridad(eventos, siem);
  } catch (e) { /* se reintenta */ }
}

function dibujarSeguridad(eventos, siem) {
  // Avisar de incidentes SIEM nuevos
  const recientes = siem.alertas.filter((s) => Date.now() - new Date(s.fecha).getTime() < 5 * 60 * 1000);
  for (const s of siem.alertas) {
    if (!estado.siemVistas.has(s.id)) {
      if (estado.siemCargado) toast("SIEM · " + s.regla, s.accion, s.severidad);
      estado.siemVistas.add(s.id);
    }
  }
  estado.siemCargado = true;
  const cuenta = $("cuenta-seguridad");
  cuenta.textContent = recientes.length;
  cuenta.classList.toggle("oculto", !recientes.length);

  $("lista-siem").innerHTML = siem.alertas.length ? siem.alertas.map((s) => `
    <div class="siem ${esc(s.severidad)}">
      <div class="cab"><span class="sev sev-${esc(s.severidad)}">${esc(s.severidad)}</span><b>${esc(s.regla)}</b><span class="suave chico">${esc(hora(s.fecha))}</span></div>
      <p>${esc(s.descripcion)}</p><p><b>Acción:</b> ${esc(s.accion)}</p>
    </div>`).join("") : '<div class="vacio">Sin incidentes. Prueba los ataques simulados desde /demo.</div>';
  $("ips-bloqueadas").innerHTML = siem.ips_bloqueadas.length
    ? "<b>IPs bloqueadas ahora:</b> " + siem.ips_bloqueadas.map((b) => `<span class="etiqueta etiqueta-rojo">${esc(b.ip)} · ${b.segundos_restantes} s</span>`).join(" ")
    : '<span class="suave">Ninguna IP bloqueada.</span>';

  $("tabla-reglas").innerHTML = (estado.reglas || []).map((r) => `
    <tr><td><b>${esc(r.nombre)}</b></td><td class="envolver">${esc(r.tipos.join(", "))} · agrupado por ${esc(r.agrupar_por)}</td>
      <td class="num">≥ ${r.umbral} en ${r.ventana_seg} s</td><td><span class="sev sev-${esc(r.severidad)}">${esc(r.severidad)}</span></td>
      <td class="envolver">${esc(r.accion)}</td></tr>`).join("");

  // El campo "Sensor" puede traer HTML de un atacante: esc() lo muestra como texto.
  $("tabla-eventos").innerHTML = eventos.map((e) => `
    <tr><td class="num">${esc(hora(e.fecha))}</td><td><span class="sev sev-${esc(e.severidad)}">${esc(e.severidad)}</span></td>
      <td>${esc(e.tipo)}</td><td class="codigo">${esc(e.ip)}</td><td class="codigo">${esc(e.sensor_id || "—")}</td>
      <td class="codigo">${esc(e.endpoint)}</td><td class="envolver">${esc(e.motivo)}</td></tr>`).join("");
}

/* =====================================================================
   11. BOTONES (un solo "escuchador" para todos, usando data-accion)
   ===================================================================== */
async function manejarClic(evento) {
  const boton = evento.target.closest("[data-accion]");
  if (!boton || boton.disabled) return;
  const id = boton.dataset.id;
  const accion = boton.dataset.accion;

  if (accion === "ver-colonia") {
    estado.coloniaSel = id;
    if (mapa) mapa.closePopup();
    dibujarTablaColonias();
    actualizarGrafica();
    if (boton.tagName === "BUTTON") $("grafica").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (accion === "ver-alerta") {
    estado.alertaSel = Number(id);
    if (mapa) mapa.closePopup();
    cambiarPestana("alertas");
    // En celular el detalle queda debajo de la lista: lo llevamos a la vista
    if (window.innerWidth < 960) $("detalle-alerta").scrollIntoView({ behavior: "smooth", block: "start" });
    else window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  const rutas = {
    aprobar: ["/api/alertas/" + id + "/aprobar", (r) => ["Firma registrada", r.colonia + " · " + (r.estado === "SEGUNDA_FIRMA" ? "falta la firma de otra persona" : "aprobada, lista para emitir")]],
    emitir: ["/api/alertas/" + id + "/emitir", (r) => ["Aviso emitido · " + r.colonia, num(r.avisos_sms) + " SMS y " + num(r.avisos_whatsapp) + " WhatsApp (simulados)"]],
    "abrir-refugio": ["/api/refugios/" + id + "/abrir", (r) => ["Refugio abierto", r.nombre]],
    liberar: ["/api/sensores/" + encodeURIComponent(id) + "/liberar", (r) => ["Sensor liberado", r.id + " volverá a usarse con su siguiente lectura"]],
    atender: ["/api/reportes/" + id + "/atender", () => ["Reporte atendido", "Se quitó del panel"]],
  };
  if (!rutas[accion]) return;
  boton.disabled = true;
  try {
    const r = await api(rutas[accion][0], { method: "POST" });
    const [titulo, texto] = rutas[accion][1](r);
    toast(titulo, texto, r.nivel || "");
  } catch (e) {
    toast("No se pudo completar", e.message, "ROJO");
  }
  await actualizar();
}

iniciar();
