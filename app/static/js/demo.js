/* =====================================================================
   demo.js - Botones para provocar situaciones durante la exposición.
   Cada botón llama a /api/demo/... con el encabezado X-Demo-Password.
   ===================================================================== */
"use strict";

const $ = (id) => document.getElementById(id);

const ACCIONES = {
  "ola-on": ["/api/demo/ola-calor", { activar: true }, "Ola de calor activada"],
  "ola-off": ["/api/demo/ola-calor", { activar: false }, "Ola de calor terminada"],
  tumbar: ["/api/demo/tumbar-sensor", {}, "Sensor tumbado"],
  falso: ["/api/demo/sensor-falso", {}, "Sensor falso enviado"],
  manipulado: ["/api/demo/sensor-manipulado", {}, "Sensor manipulado"],
  fuerza: ["/api/demo/fuerza-bruta", {}, "Ataque de fuerza bruta simulado"],
  restablecer: ["/api/demo/restablecer", {}, "Demo restablecida"],
};

function anotar(texto, detalle, error) {
  const li = document.createElement("li");
  if (error) li.className = "error";
  li.innerHTML = `<b>${esc(horaCorta(new Date().toISOString()))} · ${esc(texto)}</b><br><code>${esc(detalle)}</code>`;
  $("bitacora").prepend(li);
}

async function ejecutar(boton) {
  const [ruta, cuerpo, titulo] = ACCIONES[boton.dataset.demo];
  const password = leer("sessionStorage", "termomid_demo") || "";
  if (!password) { anotar("Falta la contraseña de demo", "Escríbela arriba y presiona Usar.", true); return; }
  boton.disabled = true;
  try {
    const r = await api(ruta, { method: "POST", body: JSON.stringify(cuerpo), headers: { "X-Demo-Password": password } });
    anotar(titulo, JSON.stringify(r));
  } catch (e) {
    anotar("Error", e.message, true);
  } finally {
    boton.disabled = false;
    actualizarEstado();
  }
}

async function actualizarEstado() {
  try {
    const e = await api("/api/estado");
    $("demo-hora").textContent = horaSimulada(e.hora_simulada);
    $("demo-ola").textContent = e.ola_calor ? "Activa (+" + e.extra_ola_hoy.toFixed(1) + " °C)" : "No";
    $("demo-dia").textContent = e.dia_segundos + " s reales";
  } catch (err) { /* se reintenta */ }
}

$("btn-guardar-password").addEventListener("click", () => {
  guardar("sessionStorage", "termomid_demo", $("demo-password").value);
  $("demo-password").value = "";
  anotar("Contraseña guardada en esta pestaña", "Ya puedes usar los botones.");
});
document.querySelectorAll("[data-demo]").forEach((b) => b.addEventListener("click", () => ejecutar(b)));
actualizarEstado();
setInterval(actualizarEstado, 3000);
