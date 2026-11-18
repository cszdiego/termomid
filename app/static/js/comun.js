/* =====================================================================
   comun.js - Funciones compartidas por todas las pantallas de TermoMID.
   ===================================================================== */
"use strict";

/* ---------- ANTI-XSS ----------
   Todo texto que viene del servidor (nombres, motivos, IDs de sensores...) pasa por esc()
   antes de ponerse en la página. Así, si un atacante manda un "sensor" llamado
   <img src=x onerror=alert(1)>, se muestra como TEXTO y nunca se ejecuta.
   Convierte & < > " ' en sus entidades HTML (&amp; &lt; &gt; &quot; &#39;). */
function esc(valor) {
  if (valor === null || valor === undefined) return "";
  return String(valor)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ---------- Fechas en hora de Mérida ----------
   El servidor guarda todo en UTC; aquí siempre se muestra en America/Merida,
   sin importar la zona horaria de la computadora o el celular que lo abra. */
const ZONA = "America/Merida";
const fmtHora = new Intl.DateTimeFormat("es-MX", { timeZone: ZONA, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const fmtHoraCorta = new Intl.DateTimeFormat("es-MX", { timeZone: ZONA, hour: "2-digit", minute: "2-digit", hour12: false });
const fmtFecha = new Intl.DateTimeFormat("es-MX", { timeZone: ZONA, weekday: "short", day: "numeric", month: "short" });

function hora(iso) { return iso ? fmtHora.format(new Date(iso)) : "—"; }
function horaCorta(iso) { return iso ? fmtHoraCorta.format(new Date(iso)) : "—"; }
function fechaHoy() { return fmtFecha.format(new Date()) + " · " + fmtHoraCorta.format(new Date()); }

/** "hace 3 min" a partir de una fecha ISO */
function hace(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "hace " + Math.round(s) + " s";
  if (s < 3600) return "hace " + Math.round(s / 60) + " min";
  return "hace " + Math.round(s / 3600) + " h";
}

/** Hora simulada (número 0-24) como "13:40" */
function horaSimulada(h) {
  const total = Math.round(h * 60) % 1440;
  return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}

/* ---------- Niveles ---------- */
const NOMBRE_NIVEL = { VERDE: "Verde", AMARILLO: "Amarillo", NARANJA: "Naranja", ROJO: "Rojo" };
// "Alerta" es femenino: alerta roja, alerta amarilla
const ALERTA_NIVEL = { VERDE: "verde", AMARILLO: "amarilla", NARANJA: "naranja", ROJO: "roja" };
const ORDEN_NIVEL = { VERDE: 0, AMARILLO: 1, NARANJA: 2, ROJO: 3 };
const COLOR_MAPA = { VERDE: "#8fcfae", AMARILLO: "#f2c94c", NARANJA: "#f0924a", ROJO: "#e05a5a" };
const COLOR_FUERTE = { VERDE: "#2f9e63", AMARILLO: "#e2b100", NARANJA: "#e8711a", ROJO: "#d43c3c" };

/** Etiqueta de nivel: SIEMPRE punto de color + texto (no depender solo del color) */
function chipNivel(nivel, texto) {
  const n = ORDEN_NIVEL[nivel] === undefined ? "VERDE" : nivel;
  return `<span class="nivel nivel-${n}">${esc(texto || NOMBRE_NIVEL[n])}</span>`;
}

function nivelDe(ic) {
  if (ic >= 51) return "ROJO";
  if (ic >= 39) return "NARANJA";
  if (ic >= 32) return "AMARILLO";
  return "VERDE";
}

const fmtNum = new Intl.NumberFormat("es-MX");
function num(n) { return n === null || n === undefined ? "—" : fmtNum.format(n); }

/* ---------- Distancia (km) entre dos coordenadas ---------- */
function distanciaKm(lat1, lon1, lat2, lon2) {
  const r = 6371, rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

/* ---------- Almacenamiento del navegador (puede fallar en modo privado) ---------- */
function guardar(almacen, clave, valor) {
  try { window[almacen].setItem(clave, valor); } catch (e) { /* sin almacenamiento: no pasa nada */ }
}
function leer(almacen, clave) {
  try { return window[almacen].getItem(clave); } catch (e) { return null; }
}
function borrar(almacen, clave) {
  try { window[almacen].removeItem(clave); } catch (e) { /* nada */ }
}

/* ---------- Notificaciones flotantes ---------- */
function toast(titulo, texto, clase) {
  let zona = document.querySelector(".avisos-flotantes");
  if (!zona) {
    zona = document.createElement("div");
    zona.className = "avisos-flotantes";
    zona.setAttribute("role", "status");
    zona.setAttribute("aria-live", "polite");
    document.body.appendChild(zona);
  }
  const t = document.createElement("div");
  t.className = "toast " + (clase || "");
  t.innerHTML = `<b>${esc(titulo)}</b><span class="chico">${esc(texto)}</span>`;
  zona.appendChild(t);
  setTimeout(() => t.remove(), 7000);
}

/* ---------- Llamadas a la API ----------
   El token de sesión se guarda en sessionStorage: se borra al cerrar la pestaña. */
async function api(ruta, opciones = {}) {
  const encabezados = Object.assign({ "Content-Type": "application/json" }, opciones.headers || {});
  const token = leer("sessionStorage", "termomid_token");
  if (token && !encabezados.Authorization) encabezados.Authorization = "Bearer " + token;
  const r = await fetch(ruta, Object.assign({}, opciones, { headers: encabezados }));
  let datos = null;
  try { datos = await r.json(); } catch (e) { /* respuesta sin JSON */ }
  if (!r.ok) {
    const error = new Error((datos && datos.detail) || ("Error " + r.status));
    error.status = r.status;
    throw error;
  }
  return datos;
}
