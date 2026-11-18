/* =====================================================================
   ciudadano.js - App ciudadana (pública, pensada para celular).

   - El ciudadano elige su colonia (se recuerda en el navegador).
   - Cada 4 s se consulta /api/ciudadano/{colonia} y se redibuja.
   - Si Protección Civil EMITE un aviso nuevo para la colonia, aparece una
     notificación arriba (como en la pantalla de bloqueo del wireframe).
   - Reportes anónimos: solo se manda la colonia y el tipo.
   ===================================================================== */
"use strict";

const $ = (id) => document.getElementById(id);
const st = { colonia: null, idioma: "es", datos: null, tipoReporte: null, primera: true };

const NIVEL_MAYA = { VERDE: "Ya'ax", AMARILLO: "K'an", NARANJA: "K'an-chak", ROJO: "Chak" };
const PELIGRO_TXT = {
  VERDE: "Sin riesgo por calor", AMARILLO: "Precaución", NARANJA: "Peligro", ROJO: "Peligro extremo: el golpe de calor puede ser mortal",
};

/* Iconos SVG fijos (no vienen del servidor, por eso es seguro ponerlos como HTML) */
const ICONOS = {
  agua: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/></svg>',
  sol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  tel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>',
  termometro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0z"/></svg>',
  rayo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
  persona: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="7" r="4"/><path d="M5 21a7 7 0 0 1 14 0"/></svg>',
  casa: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>',
  mensaje: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H8l-4 4z"/></svg>',
};

async function iniciar() {
  document.querySelectorAll("[data-icono]").forEach((el) => { el.innerHTML = ICONOS[el.dataset.icono] || ""; });

  const colonias = await api("/api/ciudadano/colonias");
  const sel = $("colonia");
  for (const c of colonias) {
    const op = document.createElement("option");
    op.value = c.id;
    op.textContent = c.nombre;  // textContent: seguro ante HTML
    sel.appendChild(op);
  }
  const guardada = leer("localStorage", "termomid_colonia");
  if (guardada && colonias.some((c) => c.id === guardada)) sel.value = guardada;
  sel.addEventListener("change", () => {
    guardar("localStorage", "termomid_colonia", sel.value);
    st.primera = true;
    actualizar();
  });

  document.querySelectorAll("[data-idioma]").forEach((b) => b.addEventListener("click", () => {
    st.idioma = b.dataset.idioma;
    document.querySelectorAll("[data-idioma]").forEach((x) => x.classList.toggle("activo", x === b));
    document.querySelectorAll("[data-maya]").forEach((el) => { el.textContent = el.dataset[st.idioma]; });
    dibujar();
  }));
  document.querySelectorAll("[data-ir]").forEach((b) => b.addEventListener("click", () => irA(b.dataset.ir)));
  $("opciones-reporte").addEventListener("click", elegirReporte);
  $("enviar-reporte").addEventListener("click", enviarReporte);
  $("notificacion").addEventListener("click", () => { $("notificacion").classList.add("oculto"); irA("colonia"); });

  actualizar();
  setInterval(actualizar, 4000);
}

function irA(vista) {
  $("vista-colonia").classList.toggle("oculto", vista !== "colonia");
  $("vista-reportar").classList.toggle("oculto", vista !== "reportar");
  document.querySelectorAll(".nav-inferior [data-ir]").forEach((b) => b.classList.toggle("activo", b.dataset.ir === vista));
  window.scrollTo({ top: 0 });
}

async function actualizar() {
  const id = $("colonia").value;
  $("sin-colonia").classList.toggle("oculto", !!id);
  $("con-colonia").classList.toggle("oculto", !id);
  $("enviar-reporte").disabled = !id || !st.tipoReporte;
  if (!id) { $("ubicacion-colonia").textContent = "Elige tu colonia"; return; }
  try {
    st.datos = await api("/api/ciudadano/" + encodeURIComponent(id));
    revisarAvisoNuevo(id);
    dibujar();
  } catch (e) { /* sin conexión: se reintenta en 4 s */ }
}

/** ¿Hay un aviso oficial que este celular no ha visto? -> notificación */
function revisarAvisoNuevo(coloniaId) {
  const aviso = st.datos.ultimo_aviso;
  if (!aviso) return;
  const huella = aviso.id + "|" + aviso.emitido;
  const clave = "termomid_visto_" + coloniaId;
  if (leer("localStorage", clave) === huella) return;
  guardar("localStorage", clave, huella);
  $("noti-titulo").textContent = "Alerta " + ALERTA_NIVEL[aviso.nivel] + " por calor en tu colonia";
  $("noti-texto").textContent = st.datos.colonia + ": índice de calor de hasta " + Math.round(aviso.indice_calor) +
    " °C. " + aviso.mensaje.es + " Toca para ver qué hacer.";
  $("notificacion").classList.remove("oculto");
  $("tarjeta-alerta").classList.add("nueva");
  setTimeout(() => $("tarjeta-alerta").classList.remove("nueva"), 9000);
  setTimeout(() => $("notificacion").classList.add("oculto"), 15000);
  try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) { /* no disponible */ }
}

function dibujar() {
  const d = st.datos;
  if (!d) return;
  const maya = st.idioma === "maya";
  const aviso = d.ultimo_aviso;
  // Se muestra el nivel más alto entre lo medido ahora y el aviso oficial vigente
  const nivel = aviso && ORDEN_NIVEL[aviso.nivel] > ORDEN_NIVEL[d.nivel] ? aviso.nivel : d.nivel;
  const nombreNivel = maya ? NIVEL_MAYA[nivel] + " (" + NOMBRE_NIVEL[nivel] + ")" : NOMBRE_NIVEL[nivel];

  $("ubicacion-colonia").textContent = d.colonia + " (aprox.)";
  const tarjeta = $("tarjeta-alerta");
  tarjeta.className = "tarjeta-alerta n-" + nivel + (tarjeta.classList.contains("nueva") ? " nueva" : "");
  $("alerta-linea").textContent = aviso
    ? "Alerta " + (maya ? NIVEL_MAYA[nivel] : ALERTA_NIVEL[nivel]) + " · aviso oficial"
    : "Nivel ahora: " + nombreNivel;
  const ahoraTxt = d.indice_calor !== null ? Math.round(d.indice_calor) + " °C" : "— °C";
  if (aviso) {
    // Con aviso oficial: el número grande es el máximo del aviso y "ahora" va aparte
    $("alerta-ic").textContent = "hasta " + Math.round(aviso.indice_calor) + " °C";
    $("alerta-sub").textContent = "Índice de calor máximo · " + PELIGRO_TXT[aviso.nivel] +
      ". Ahora: " + ahoraTxt + " (" + NOMBRE_NIVEL[d.nivel] + ")";
  } else {
    $("alerta-ic").textContent = ahoraTxt;
    $("alerta-sub").textContent = "Índice de calor ahora · " + PELIGRO_TXT[d.nivel];
  }
  $("alerta-rec").textContent = (aviso ? aviso.mensaje : d.recomendacion)[st.idioma];

  const caja = $("aviso-oficial");
  if (aviso) {
    caja.className = "aviso-oficial l-" + aviso.nivel;
    caja.innerHTML = `<b>Aviso de Protección Civil · emitido a las ${esc(horaCorta(aviso.emitido))}</b>
      Alerta ${esc(ALERTA_NIVEL[aviso.nivel])} por calor en ${esc(d.colonia)}. Índice de calor de hasta ${esc(Math.round(aviso.indice_calor))} °C.`;
  } else {
    caja.className = "aviso-oficial oculto";
  }

  $("horas").innerHTML = d.proximas_horas.slice(0, 6).map((p) =>
    `<div class="hora-chip n-${esc(p.nivel)}">${esc(Math.floor(p.hora_del_dia))} h<b>${esc(Math.round(p.indice_calor))}°</b></div>`).join("");
  $("manana").textContent = "Mañana (máximo): " + Math.round(d.manana.indice_calor) + " °C · nivel " +
    (maya ? NIVEL_MAYA[d.manana.nivel] : NOMBRE_NIVEL[d.manana.nivel]) + ". Horas simuladas (1 día = 4 min).";

  $("consejos").innerHTML = d.consejos.map((c) =>
    `<li><span class="ico">${ICONOS[c.icono] || ""}</span><span>${esc(c[st.idioma])}</span></li>`).join("");

  const r = d.refugio_cercano;
  $("refugio").innerHTML = r ? `<span class="suave">Refugio climático más cercano · ${esc(r.distancia_km)} km</span>
      <b>${esc(r.nombre)}</b>
      <span class="suave">${r.abierto ? "Abierto ahora" : "Cerrado por ahora (se abre con la alerta)"} · ${esc(r.lugares_libres)} lugares libres · agua y aire acondicionado</span>`
    : '<span class="suave">No hay refugios con lugar disponible.</span>';
  $("como-llegar").href = r ? `https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lon}#map=17/${r.lat}/${r.lon}` : "#";
}

function elegirReporte(evento) {
  const op = evento.target.closest(".opcion");
  if (!op) return;
  st.tipoReporte = op.dataset.tipo;
  document.querySelectorAll(".opcion").forEach((x) => x.classList.toggle("sel", x === op));
  $("enviar-reporte").disabled = !$("colonia").value;
}

async function enviarReporte() {
  const salida = $("reporte-resultado");
  $("enviar-reporte").disabled = true;
  try {
    const r = await api("/api/ciudadano/reportes", {
      method: "POST", body: JSON.stringify({ colonia_id: $("colonia").value, tipo: st.tipoReporte }),
    });
    salida.className = "reporte-resultado";
    salida.textContent = r.mensaje;
    st.tipoReporte = null;
    document.querySelectorAll(".opcion").forEach((x) => x.classList.remove("sel"));
  } catch (e) {
    salida.className = "reporte-resultado error";
    salida.textContent = e.message;
  }
}

iniciar();
