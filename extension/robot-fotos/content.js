// ==UserScript==
// @name         Robot validación de fotos (MiFibra)
// @namespace    mifibra
// @version      1.6
// @description  Botón dentro de SCORD: escribes el AB y revisa las fotos de la instalación contra el manual.
// @match        https://mifibra.scordsoft.net/*
// @grant        none
// ==/UserScript==
(function () {
  "use strict";
  if (window.__mfvCargado) return;
  window.__mfvCargado = true;

  // ------------------------------------------------------------------ configuración
  const PANTALLA_OS = "/CTecnico/TECMantOrdenServicio.aspx";
  const FILIAL_LIMA = "9";
  const TIPO = "INSTALACION SERVICIO INTERNET";
  const PERDIDA_MAX_DB = 1.0;   // foto 2: pérdida máxima entre caja y cliente
  const DIAS_TOLERANCIA = 1;    // fotos del día anterior son válidas
  const LADO_MAX = 1280;        // px: se achican las fotos antes de enviarlas a la IA (más liviano = respuesta más rápida)

  const MANUAL = [
    [1, "Fachada del cliente", ["Fachada de la vivienda fácil de identificar (número de puerta visible)", "Sello con localización, fecha y hora"]],
    [2, "Potencia en NAP y en cliente", ["Medición de potencia en la NAP dentro de rangos aceptables (aprox. -15 a -25 dBm)", "Se ve que la medición se hizo en ambos lugares (NAP y domicilio del cliente)", "Valores del power meter legibles", `Pérdida entre caja y cliente de máximo ${PERDIDA_MAX_DB} dB`, "Sello con localización, fecha y hora"]],
    [3, "NAP cerrada y DROP conectado al borne", ["Serie/código de la CTO visible", "CTO/NAP cerrada", "DROP conectado en la parte inferior de la CTO", "Cintillo visible", "Sello con localización, fecha y hora"]],
    [4, "Ubicación del router (ONU)", ["ONU fijada en la pared del cliente (no sobre mesa/escritorio)", "Estética correcta, a 1.5–2 m sobre el piso", "Sello con localización, fecha y hora"]],
    [5, "DNI y boletín", ["Datos del DNI visibles claramente", "Boletín con firma del cliente y del técnico", "Sello con localización, fecha y hora"]],
    [6, "Borne y cintillo rosado", ["Cintillo rosado claramente visible", "Borne utilizado identificable", "Sello con localización, fecha y hora"]],
    [7, "Speedtest desde la computadora del cliente", ["Prueba con la app Speedtest, con adaptador Ethernet", "Si el cliente no tiene dispositivo, evidencia de ello", "Sello con hora, fecha y geolocalización"]],
    [8, "Speedtest desde el dispositivo del técnico (de preferencia laptop)", ["Desde aplicativo o navegador incógnito", "Si es dispositivo de gama baja, evidencia del dispositivo", "Sello con hora, fecha y geolocalización"]],
    [9, "WiFi (2.4G y 5G) y remoto activo", ["Configuración WiFi 2.4G y 5G visible correctamente", "Acceso remoto activo visible"]],
    [10, "Vinculación MESH y potencia MESH", ["Topología/vinculación del MESH con el router", "Potencia del MESH legible: -30 a -50 excelente, -51 a -60 muy buena, -61 a -70 buena, -71 o menos débil (débil = error)"], "mesh"],
    [11, "Mapa de CTO que brinda el gestor", ["Captura del mapa de CTOs del gestor (no requiere sello)"]],
    [12, "Envío de parámetros", ["Captura del envío de parámetros por parte del gestor MF (no requiere sello)"]],
  ];

  // ------------------------------------------------------------------ estado (sobrevive a las recargas de SCORD)
  const K = "mfv-estado", KR = "mfv-resultado", KKEY = "mfv-gemini-key", KOR = "mfv-openrouter-key", KC = "mfv-cache", KSH = "mfv-sheets-url";
  const leer = (k) => { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch { return null; } };
  const guardar = (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, JSON.stringify(v)); } catch {} };
  const clave = () => { try { return localStorage.getItem(KKEY) || ""; } catch { return ""; } };
  const claveOR = () => { try { return localStorage.getItem(KOR) || ""; } catch { return ""; } };
  const urlSheets = () => { try { return localStorage.getItem(KSH) || ""; } catch { return ""; } };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (s) => document.querySelector(s);

  // ------------------------------------------------------------------ interfaz
  const css = `
  #mfv-btn{position:fixed;right:18px;bottom:64px;z-index:99998;background:#16a34a;color:#fff;border:0;border-radius:999px;
    padding:10px 16px;font:600 13px system-ui;box-shadow:0 4px 14px rgba(0,0,0,.25);cursor:pointer}
  #mfv-panel{position:fixed;right:18px;bottom:110px;z-index:99999;width:min(560px,calc(100vw - 36px));max-height:75vh;overflow:auto;
    background:#fff;color:#111;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.3);font:13px system-ui;display:none}
  #mfv-panel header{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;background:#0f172a;color:#fff;border-radius:12px 12px 0 0}
  #mfv-panel .cuerpo{padding:12px 14px}
  #mfv-panel input{padding:8px;border:1px solid #cbd5e1;border-radius:6px;font:14px system-ui}
  #mfv-panel button.acc{background:#16a34a;color:#fff;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer}
  #mfv-panel button.sec{background:#e2e8f0;border:0;border-radius:6px;padding:6px 10px;cursor:pointer}
  #mfv-panel table{border-collapse:collapse;width:100%;margin-top:8px}
  #mfv-panel td,#mfv-panel th{border:1px solid #e2e8f0;padding:5px;text-align:left;vertical-align:top}
  #mfv-panel th{background:#f1f5f9}
  .mfv-OK{color:#16a34a;font-weight:700}.mfv-ERROR{color:#dc2626;font-weight:700}.mfv-FALTA{color:#b45309;font-weight:700}.mfv-NO{color:#64748b}
  #mfv-log{color:#475569;margin-top:8px;white-space:pre-wrap}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  const btn = document.createElement("button");
  btn.id = "mfv-btn"; btn.type = "button"; btn.textContent = "🤖 Validar fotos";
  const panel = document.createElement("div"); panel.id = "mfv-panel";
  panel.innerHTML = `
    <header><b>Robot validación de fotos</b><span><button class="sec" id="mfv-cfg" title="Clave de Gemini">⚙</button>
      <button class="sec" id="mfv-x">✕</button></span></header>
    <div class="cuerpo">
      <div id="mfv-clave" style="display:none;margin-bottom:10px">
        Clave de Gemini (se guarda solo en este Chrome):<br>
        <input id="mfv-key" type="password" style="width:70%" placeholder="AIza…"><br>
        <span style="display:block;margin-top:6px">Respaldo gratis OpenRouter (opcional, se usa si Gemini está saturado):</span>
        <input id="mfv-keyor" type="password" style="width:70%" placeholder="sk-or-…"><br>
        <span style="display:block;margin-top:6px">Guardar resultados en Google Sheets (opcional, URL de la aplicación web):</span>
        <input id="mfv-sheets" type="text" style="width:70%" placeholder="https://script.google.com/macros/s/…/exec">
        <button class="acc" id="mfv-keyok">Guardar</button>
        <div id="mfv-estado-claves" style="color:#64748b;margin-top:4px"></div>
      </div>
      <div>Nº de abonado: <input id="mfv-ab" style="width:120px" inputmode="numeric">
        <button class="acc" id="mfv-go">Validar</button></div>
      <div id="mfv-log"></div>
      <div id="mfv-res"></div>
    </div>`;
  document.body.append(btn, panel);
  const log = (t) => { $("#mfv-log").textContent = t; };
  const abrir = () => { panel.style.display = "block"; if (!clave()) { $("#mfv-clave").style.display = "block"; estadoClaves(); } };
  btn.onclick = () => (panel.style.display === "block" ? (panel.style.display = "none") : abrir());
  $("#mfv-x").onclick = () => (panel.style.display = "none");
  const estadoClaves = () => { $("#mfv-estado-claves").textContent =
    `Gemini: ${clave() ? "guardada ✓" : "falta"} · OpenRouter: ${claveOR() ? "guardada ✓" : "no configurada"} · Google Sheets: ${urlSheets() ? "conectado ✓" : "no configurado"}`; };
  $("#mfv-cfg").onclick = () => { const d = $("#mfv-clave"); d.style.display = d.style.display === "none" ? "block" : "none"; estadoClaves(); };
  $("#mfv-keyok").onclick = () => {
    const v = $("#mfv-key").value.trim(), o = $("#mfv-keyor").value.trim(), sh = $("#mfv-sheets").value.trim();
    if (!v && !o && !sh) return;
    if (sh && !/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec/.test(sh)) return log("La URL de Google Sheets debe ser la de la aplicación web (termina en /exec).");
    try { if (v) localStorage.setItem(KKEY, v); if (o) localStorage.setItem(KOR, o); if (sh) localStorage.setItem(KSH, sh); } catch {}
    $("#mfv-key").value = ""; $("#mfv-keyor").value = ""; $("#mfv-sheets").value = ""; estadoClaves(); $("#mfv-clave").style.display = "none"; log("Claves guardadas.");
  };
  $("#mfv-go").onclick = () => {
    const ab = ($("#mfv-ab").value || "").replace(/\D/g, "");
    if (!ab) return log("Escribe el Nº de abonado.");
    if (!clave()) { $("#mfv-clave").style.display = "block"; return log("Primero guarda la clave de Gemini."); }
    guardar(KR, null);
    guardar(K, { ab, paso: "filial", t: Date.now() });
    log(`Buscando AB ${ab}…`);
    location.href = PANTALLA_OS;
  };
  $("#mfv-ab").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#mfv-go").click(); } });

  // ------------------------------------------------------------------ resultado
  function pintar(res) {
    abrir();
    if (res.ab) $("#mfv-ab").value = res.ab;
    if (res.error) { $("#mfv-res").innerHTML = `<p class="mfv-ERROR">AB ${res.ab}: ${esc(res.error)}</p>`; log(""); return; }
    const v = res.validacion || {};
    const filas = (v.manual || []).map((m) => `<tr><td>${m.item}</td><td>${esc(m.nombre)}</td>
      <td class="mfv-${m.estado === "NO APLICA" ? "NO" : m.estado}">${m.estado}</td>
      <td>${(m.fotos || []).join(", ") || "—"}</td><td>${esc(m.error || "")}</td></tr>`).join("");
    const color = v.resultado === "OK" ? "mfv-OK" : "mfv-ERROR";
    $("#mfv-res").innerHTML = `
      <h3 style="margin:10px 0 4px">AB ${res.ab} · OS ${esc(res.os || "")} — <span class="${color}">${v.resultado || "?"}</span></h3>
      <div>Ejecución ${esc(res.fecha || "")} · ${res.n} fotos${v.perdida_db != null ? ` · pérdida caja–cliente ${v.perdida_db} dB` : ""}</div>
      <p><b>${esc(v.resumen || "")}</b></p>
      <table><tr><th>#</th><th>Foto del manual</th><th>Estado</th><th>Fotos</th><th>Error</th></tr>${filas}</table>`;
    log(`Revisado con ${res.modelo || "IA"}.`);
  }
  // Envía el resultado a la hoja de Google Sheets del usuario (una fila por validación nueva)
  async function enviarSheets(r) {
    const url = urlSheets(); if (!url || !r || r.error) return "";
    const v = r.validacion || {};
    const estados = {}; (v.manual || []).forEach((m) => { estados["F" + m.item] = m.estado; });
    const usuario = ((document.querySelector("a[href*='CambioClaveEmpleado']") || {}).textContent || "").trim();
    const fila = {
      fecha_revision: new Date().toLocaleString("es-PE"), ab: r.ab, os: r.os, fecha_ejecucion: r.fecha || "", tecnico: r.tecnico || "",
      n_fotos: r.n, resultado: v.resultado || "", perdida_db: v.perdida_db ?? "",
      errores: (v.manual || []).filter((m) => m.estado === "ERROR" || m.estado === "FALTA").map((m) => `Foto ${m.item} ${m.estado}: ${m.error}`).join(" | "),
      resumen: v.resumen || "", ia: r.modelo || "", revisado_por: usuario, ...estados,
    };
    try {
      // text/plain evita el bloqueo del navegador; Apps Script lo recibe igual
      await fetch(url, { method: "POST", mode: "no-cors", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(fila) });
      return " · Enviado a Google Sheets ✓";
    } catch { return " · No se pudo enviar a Google Sheets"; }
  }

  // Si la misma OS con las mismas fotos ya se revisó, se muestra al instante (sin volver a usar la IA).
  const cacheLeer = () => { try { return JSON.parse(localStorage.getItem(KC) || "{}"); } catch { return {}; } };
  function cacheGuardar(k, r) {
    try {
      const c = cacheLeer(); c[k] = { ...r, guardado: Date.now() };
      const claves = Object.keys(c).sort((a, b) => c[a].guardado - c[b].guardado);
      while (claves.length > 300) delete c[claves.shift()];
      localStorage.setItem(KC, JSON.stringify(c));
    } catch {}
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ------------------------------------------------------------------ pasos en SCORD
  function campo(etiqueta) {
    const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().trim();
    for (const td of document.querySelectorAll("td, label, span, div")) {
      if (td.children.length > 2 || norm(td.textContent) !== norm(etiqueta)) continue;
      let n = td.nextElementSibling;
      for (let i = 0; n && i < 3; i++, n = n.nextElementSibling) {
        const c = n.matches("input,textarea,select") ? n : n.querySelector("input:not([type=hidden]),textarea,select");
        if (c) return c.tagName === "SELECT" ? (c.options[c.selectedIndex] || {}).text || "" : c.value;
      }
    }
    return "";
  }
  // ¿La página se va a recargar (postback)? Se espera solo lo necesario en vez de tiempos fijos.
  const seRecarga = (ms) => new Promise((ok) => {
    let fin = false;
    const si = () => { if (!fin) { fin = true; ok(true); } };
    window.addEventListener("beforeunload", si, { once: true });
    window.addEventListener("pagehide", si, { once: true });
    setTimeout(() => { if (!fin) { fin = true; ok(false); } }, ms);
  });
  const KOS = "mfv-os-de-ab"; // AB → OS de instalación ya encontrada antes
  const osConocida = (ab) => { try { return (JSON.parse(localStorage.getItem(KOS) || "{}"))[ab] || ""; } catch { return ""; } };
  const recordarOS = (ab, os) => { try { const m = JSON.parse(localStorage.getItem(KOS) || "{}"); m[ab] = os; localStorage.setItem(KOS, JSON.stringify(m)); } catch {} };
  const fecha = (d) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
  function terminar(e, error) { guardar(K, null); const r = { ab: e.ab, error }; guardar(KR, r); pintar(r); }

  async function continuar() {
    const e = leer(K);
    if (!e) { const r = leer(KR); if (r && r.mostrar !== false) { pintar(r); r.mostrar = false; guardar(KR, r); } return; }
    if (Date.now() - e.t > 10 * 60 * 1000) return terminar(e, "se canceló (más de 10 min).");
    abrir(); $("#mfv-ab").value = e.ab;
    const ruta = location.pathname.toLowerCase();

    if (ruta.endsWith(PANTALLA_OS.toLowerCase())) {
      const fil = $("#MainContent_cboFilial");
      if (!fil) return terminar(e, "no se encontró el formulario de órdenes.");
      if (e.paso === "filial") {
        if (fil.value !== FILIAL_LIMA) {
          guardar(K, { ...e, paso: "filial2" });
          fil.value = FILIAL_LIMA; fil.dispatchEvent(new Event("change", { bubbles: true }));
          if (await seRecarga(2000)) return; // la página se recarga y el robot sigue solo
        }
        e.paso = "buscar";
      }
      if (e.paso === "filial2") e.paso = "buscar";
      if (e.paso === "buscar") {
        log(`Buscando AB ${e.ab}…`);
        $("#MainContent_txtNroAbonado").value = String(parseInt(e.ab, 10));
        const hoy = new Date(), ini = new Date(Date.now() - 90 * 864e5);
        $("#MainContent_txtFechaInicio").value = fecha(ini);
        $("#MainContent_txtFechaTermino").value = fecha(hoy);
        // Si ya se conoce la OS de instalación de este AB, se busca directo por N° de OS (1 sola fila)
        const osDir = !e.sinDirecto && !(e.usadas || []).length ? osConocida(e.ab) : "";
        const campoOS = $("#MainContent_txtNroOrdenServicio");
        if (campoOS) campoOS.value = osDir;
        guardar(K, { ...e, paso: "elegir", directo: !!osDir });
        $("#MainContent_btnBuscar").click();
        if (await seRecarga(2500)) return;
        for (let i = 0; i < 40; i++) { // búsqueda sin recarga: se sigue apenas aparecen las filas
          if (leer(K)?.paso === "elegir" && document.querySelector("input[id^=RowSelector]")) return continuar();
          await espera(250);
        }
        return;
      }
      if (e.paso === "elegir") {
        const filas = [...document.querySelectorAll("input[id^=RowSelector]")].map((r) => {
          const t = [...r.closest("tr").querySelectorAll("td")].map((td) => td.innerText.trim());
          return { radio: r, celdas: t, texto: t.join(" ") };
        }).filter((f) => f.texto.toUpperCase().includes(TIPO) && new RegExp(`\\b0*${parseInt(e.ab, 10)}\\b`).test(f.texto));
        const usadas = e.usadas || [];
        const cand = filas.filter((f) => /DESCARGADA/i.test(f.texto)).concat(filas.filter((f) => !/DESCARGADA/i.test(f.texto)))
          .filter((f) => !usadas.includes((f.texto.match(/\b\d{8}\b/) || [""])[0]));
        if (!cand.length && e.directo) { guardar(K, { ...e, paso: "filial", sinDirecto: true, directo: false }); location.href = PANTALLA_OS; return; }
        if (!cand.length) return terminar(e, usadas.length ? "ninguna OS de instalación tiene capturas del técnico." : "no tiene OS de instalación (últimos 90 días).");
        const f = cand[0];
        const os = (f.texto.match(/\b\d{8}\b/) || [""])[0];
        log(`Abriendo OS ${os}…`);
        f.radio.click();
        guardar(K, { ...e, paso: "fotos", os, dir: f.celdas[f.celdas.length - 2] || "", usadas: [...usadas, os], t: Date.now() });
        await espera(300);
        const consultar = [...document.querySelectorAll("input[type=submit],input[type=button],button")].find((b) => /^consultar$/i.test((b.value || b.textContent || "").trim()));
        if (!consultar) return terminar(e, "no se encontró el botón Consultar.");
        consultar.click();
        return;
      }
    }

    if (e.paso === "fotos" && $("#btnverimagen_tecnico") == null) {
      if (/Ejecucion/i.test(location.pathname)) { guardar(K, { ...e, paso: "filial", sinDirecto: true }); location.href = PANTALLA_OS; } // sin capturas: probar otra OS
      return;
    }

    if (e.paso === "fotos") {
      const datos = {
        ab: e.ab, os: e.os, dir: e.dir,
        fecha: campo("FECHA DE EJECUCION"), tecnico: campo("TECNICO EJECUTOR"), obs: campo("OBS DEL TECNICO"),
        hi: campo("HORA INICIO EJEC"), hf: campo("HORA TÉRMINO EJEC") || campo("HORA TERMINO EJEC"),
      };
      log(`OS ${e.os}: descargando fotos…`);
      const antes = performance.getEntriesByType("resource").length;
      $("#btnverimagen_tecnico").click();
      let urls = [], quieto = 0;
      for (let i = 0; i < 100 && quieto < 5; i++) {
        await espera(300);
        const n = [...new Set(performance.getEntriesByType("resource").slice(antes).map((x) => x.name).filter((u) => /downloadpageOS\.aspx/i.test(u)))];
        if (n.length === urls.length && n.length) quieto++; else quieto = 0;
        urls = n;
      }
      // Cierra la galería para que no tape el resultado.
      document.querySelectorAll(".ui-dialog-titlebar-close, .close, [aria-label=Close]").forEach((x) => { try { x.click(); } catch {} });
      if (!urls.length) { guardar(K, { ...e, paso: "filial", sinDirecto: true }); location.href = PANTALLA_OS; return; }
      recordarOS(e.ab, e.os);
      const ck = `${e.ab}|${e.os}|${urls.length}`, previo = cacheLeer()[ck];
      if (previo) {
        guardar(K, null);
        const r = { ...previo, modelo: `${previo.modelo} · guardado del ${new Date(previo.guardado).toLocaleDateString("es-PE")}`, mostrar: true };
        guardar(KR, r); pintar(r); return;
      }
      try {
        // Todas las fotos se descargan a la vez (antes, una por una).
        const fotos = await Promise.all(urls.map(async (u) => achicar(await (await fetch(u, { credentials: "include" })).blob())));
        log(`OS ${e.os}: ${fotos.length} fotos. Revisando con la IA (puede tardar 1–3 min)…`);
        const { v, modelo } = await revisarIA(datos, fotos);
        guardar(K, null);
        const r = { ab: e.ab, os: e.os, fecha: datos.fecha, tecnico: datos.tecnico, n: fotos.length, validacion: v, modelo, mostrar: true };
        cacheGuardar(ck, { ...r, mostrar: undefined });
        guardar(KR, r); pintar(r);
        const env = await enviarSheets(r);
        if (env) log(`Revisado con ${modelo}.${env}`);
      } catch (err) {
        terminar(e, `error al revisar: ${err.message || err}`);
      }
    }
  }

  // ------------------------------------------------------------------ IA (Gemini)
  function achicar(blob) {
    return new Promise((ok, mal) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, LADO_MAX / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        ok(c.toDataURL("image/jpeg", 0.85).split(",")[1]);
      };
      img.onerror = () => mal(new Error("no se pudo leer una foto"));
      img.src = URL.createObjectURL(blob);
    });
  }

  function prompt(d, n) {
    const mesh = /MESH/i.test(d.obs || "");
    const manual = MANUAL.filter((m) => m[3] !== "mesh" || mesh).map((m) => `Foto ${m[0]} – ${m[1]}:\n${m[2].map((x) => "   - " + x).join("\n")}`).join("\n");
    return `Eres auditor de calidad de instalaciones de fibra óptica (MiFibra, Lima). Revisa las ${n} fotos adjuntas
(en orden: foto 1, foto 2, …) que subió el técnico para liquidar esta orden y compáralas con el MANUAL.

Datos de la orden:
- OS: ${d.os} · Abonado: ${d.ab}
- Dirección del cliente: ${d.dir}
- Fecha de ejecución: ${d.fecha} (hora inicio ${d.hi}, término ${d.hf})
- Técnico: ${d.tecnico}
- Observación del técnico: ${d.obs}
- La orden ${mesh ? "SÍ" : "NO"} tiene MESH.

MANUAL (cada ítem es una foto obligatoria; una foto subida puede ser un collage que cubra varios ítems):
${manual}

Reglas:
- Fecha del sello: válida si es el día de ejecución o hasta ${DIAS_TOLERANCIA} día antes. Más antigua o posterior = ERROR.
- Foto 2: lee la potencia (dBm, valor negativo) medida en la NAP y la medida en el cliente y devuélvelas en
  potencia_nap_dbm y potencia_cliente_dbm (null si no se puede leer).
- Mapa de CTO y envío de parámetros son capturas del gestor: no requieren sello.
- Si un ítem no aparece en ninguna foto: estado FALTA.
- Si aparece pero no cumple algún punto: ERROR, y en "error" explica exactamente qué falta o está mal (breve, en español).
- Si cumple todo: OK con error vacío. Ítem MESH sin MESH en la orden: NO APLICA.
- No inventes: si algo no se puede leer en la foto, es ERROR ("no legible").
- resultado = OK solo si todos los ítems están OK o NO APLICA; si no, OBSERVADA.
- resumen: una o dos frases para el supervisor.
Responde solo con el JSON pedido.`;
  }

  const ESQUEMA = {
    type: "OBJECT",
    properties: {
      manual: { type: "ARRAY", items: { type: "OBJECT", properties: {
        item: { type: "INTEGER" }, nombre: { type: "STRING" },
        estado: { type: "STRING", enum: ["OK", "ERROR", "FALTA", "NO APLICA"] },
        fotos: { type: "ARRAY", items: { type: "INTEGER" } }, error: { type: "STRING" } },
        required: ["item", "nombre", "estado", "fotos", "error"] } },
      potencia_nap_dbm: { type: "NUMBER", nullable: true },
      potencia_cliente_dbm: { type: "NUMBER", nullable: true },
      resultado: { type: "STRING", enum: ["OK", "OBSERVADA"] },
      resumen: { type: "STRING" },
    },
    required: ["manual", "resultado", "resumen"],
  };

  async function modelosDisponibles(key) {
    try {
      const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=${encodeURIComponent(key)}`);
      const j = await r.json();
      const malos = ["image", "tts", "audio", "live", "embedding", "thinking", "exp", "omni", "preview"]; // "lite" queda al final
      return (j.models || []).filter((m) => (m.supportedGenerationMethods || []).includes("generateContent"))
        .map((m) => m.name.split("/")[1]).filter((n) => n.includes("flash") && !malos.some((x) => n.includes(x)))
        .sort((a, b) => (a.includes("lite") - b.includes("lite")) || b.localeCompare(a));
    } catch { return []; }
  }

  // Llama a un modelo de Gemini. Devuelve el resultado o lanza un error (err.fatal = clave inválida).
  // Razonamiento corto = respuesta mucho más rápida. Se prueba el ajuste que acepte cada modelo y se recuerda.
  const PENSAR = [{ thinkingLevel: "low" }, { thinkingBudget: 512 }, null];
  const KTH = (m) => "mfv-pensar-" + m;
  async function llamarGemini(modelo, key, cuerpoBase, senal) {
    let v = 0; try { v = +(localStorage.getItem(KTH(modelo)) || 0); } catch {}
    for (let i = 0; i < 4; i++) {
      const gc = { ...cuerpoBase.generationConfig };
      if (PENSAR[v]) gc.thinkingConfig = PENSAR[v];
      const cuerpo = JSON.stringify({ ...cuerpoBase, generationConfig: gc });
      const ctl = new AbortController();
      const to = setTimeout(() => ctl.abort(), 90000); // 90 s por modelo
      const cortar = () => ctl.abort();
      senal.addEventListener("abort", cortar);
      let r;
      try {
        r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${modelo}:generateContent?key=${encodeURIComponent(key)}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: cuerpo, signal: ctl.signal });
      } catch { throw new Error(`${modelo}: tiempo agotado`); } finally { clearTimeout(to); senal.removeEventListener("abort", cortar); }
      if ([429, 500, 503].includes(r.status) && i === 0 && !senal.aborted) { await espera(5000); continue; }
      if (r.status === 400) {
        const t = await r.text();
        if (/API key not valid|API_KEY_INVALID/i.test(t)) { const e = new Error("la clave de Gemini no es válida (revísala en ⚙)."); e.fatal = true; throw e; }
        // el modelo no acepta ese ajuste de razonamiento: se prueba el siguiente
        if (/think/i.test(t) && v < PENSAR.length - 1) { v++; try { localStorage.setItem(KTH(modelo), String(v)); } catch {} continue; }
        throw new Error(`${modelo}: 400`);
      }
      if (!r.ok) throw new Error(`${modelo}: ${r.status}`);
      const j = await r.json();
      const texto = (j.candidates[0].content.parts || []).filter((p) => !p.thought && p.text).map((p) => p.text).join("");
      return { v: aplicarPerdida(JSON.parse(texto)), modelo };
    }
    throw new Error(`${modelo}: saturado`);
  }

  // Carrera: se consulta a varios modelos a la vez y se usa el primero que responda bien; los demás se cancelan.
  async function carrera(modelos, fn) {
    const ctl = new AbortController();
    try {
      return await Promise.any(modelos.map((m) => fn(m, ctl.signal)));
    } catch (agg) {
      const fatal = (agg.errors || []).find((x) => x && x.fatal);
      if (fatal) throw fatal;
      throw new Error((agg.errors || []).map((x) => x && x.message).join(" · "));
    } finally { ctl.abort(); }
  }

  async function revisarIA(d, fotos) {
    const key = clave();
    const partes = [{ text: prompt(d, fotos.length) }];
    fotos.forEach((b64, i) => partes.push({ text: `Foto ${i + 1}:` }, { inline_data: { mime_type: "image/jpeg", data: b64 } }));
    const cuerpo = { contents: [{ role: "user", parts: partes }],
      generationConfig: { temperature: 0, responseMimeType: "application/json", responseSchema: ESQUEMA } };
    let ultimo = "";
    if (key) {
      const limite = Date.now() + 4 * 60 * 1000; // máx. 4 min con Gemini; después se pasa al respaldo
      const probados = new Set();
      // Ronda 1: los dos modelos que mejor funcionaron, a la vez.
      let ronda = ["gemini-3.6-flash", "gemini-flash-latest"];
      let otros = null;
      while (ronda.length && Date.now() < limite) {
        ronda.forEach((m) => probados.add(m));
        log(`Revisando con la IA (${ronda.join(" + ")})…`);
        try {
          return await carrera(ronda, (m, senal) => llamarGemini(m, key, cuerpo, senal));
        } catch (err) {
          if (err.fatal) throw err;
          ultimo = err.message;
        }
        if (!otros) otros = (await modelosDisponibles(key)).filter((m) => !probados.has(m)); // flash normales y al final flash-lite
        ronda = otros.splice(0, 2);
      }
    }
    if (claveOR()) {
      log("Gemini saturado: probando el respaldo gratis (OpenRouter)…");
      return revisarOpenRouter(d, fotos, ultimo);
    }
    throw new Error(`ningún modelo de Gemini respondió (${ultimo}). Intenta en unos minutos o configura el respaldo OpenRouter en ⚙.`);
  }

  // Regla de pérdida de la foto 2: se calcula aquí, no se deja a la IA.
  function aplicarPerdida(v) {
    if (v.potencia_nap_dbm != null && v.potencia_cliente_dbm != null) {
      const p = Math.round(Math.abs(v.potencia_nap_dbm - v.potencia_cliente_dbm) * 100) / 100;
      v.perdida_db = p;
      if (p > PERDIDA_MAX_DB) {
        const m = (v.manual || []).find((x) => x.item === 2);
        if (m) { const t = `Pérdida caja–cliente de ${p} dB (NAP ${v.potencia_nap_dbm}, cliente ${v.potencia_cliente_dbm}); máximo ${PERDIDA_MAX_DB} dB.`;
          m.estado = "ERROR"; m.error = m.error ? `${t} ${m.error}` : t; }
        v.resultado = "OBSERVADA";
      }
    }
    return v;
  }

  // ------------------------------------------------------------------ respaldo gratis: OpenRouter (modelos ":free" con visión)
  const FORMATO_JSON = `
Responde SOLO con un objeto JSON (sin texto antes ni después) con esta forma:
{"manual":[{"item":1,"nombre":"Fachada del cliente","estado":"OK|ERROR|FALTA|NO APLICA","fotos":[1],"error":""}, … un objeto por cada ítem del manual],
 "potencia_nap_dbm": número o null, "potencia_cliente_dbm": número o null,
 "resultado":"OK|OBSERVADA", "resumen":"…"}`;

  // Primero los modelos que mejor leen fotos (Gemma de Google, Nemotron VL, Qwen VL, Llama 4); luego el resto.
  const prioridad = (id) => { const orden = [/gemma-4-31b/, /gemma/, /nemotron.*vl/, /qwen.*vl/, /llama-4/, /mistral|pixtral/];
    const i = orden.findIndex((r) => r.test(id)); return i < 0 ? 99 : i; };

  async function modelosGratisVision() {
    try {
      const j = await (await fetch("https://openrouter.ai/api/v1/models")).json();
      return (j.data || []).filter((m) => m.id.endsWith(":free") &&
          ((m.architecture && (m.architecture.input_modalities || []).includes("image")) || /image/.test((m.architecture || {}).modality || "")))
        .filter((m) => !/safety|guard|moderat/i.test(m.id))
        .map((m) => m.id)
        .sort((a, b) => prioridad(a) - prioridad(b));
    } catch { return []; }
  }

  function extraerJSON(t) {
    const i = t.indexOf("{"), f = t.lastIndexOf("}");
    if (i < 0 || f < i) throw new Error("la IA no devolvió JSON");
    return JSON.parse(t.slice(i, f + 1));
  }

  async function revisarOpenRouter(d, fotos, previo) {
    const key = claveOR();
    const modelos = (await modelosGratisVision()).slice(0, 4);
    if (!modelos.length) throw new Error(`Gemini saturado (${previo}) y no hay modelos gratis con visión en OpenRouter ahora.`);
    const contenido = [{ type: "text", text: prompt(d, fotos.length) + FORMATO_JSON }];
    fotos.forEach((b64, i) => contenido.push({ type: "text", text: `Foto ${i + 1}:` },
      { type: "image_url", image_url: { url: `data:image/jpeg;base64,${b64}` } }));
    const una = async (modelo, senal) => {
      const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 120000);
      const cortar = () => ctl.abort(); senal.addEventListener("abort", cortar);
      try {
        const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
          method: "POST", signal: ctl.signal,
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}`, "X-Title": "Robot validacion fotos" },
          body: JSON.stringify({ model: modelo, temperature: 0, messages: [{ role: "user", content: contenido }] }),
        });
        if (r.status === 401) { const e = new Error("la clave de OpenRouter no es válida (revísala en ⚙)."); e.fatal = true; throw e; }
        if (!r.ok) throw new Error(`${modelo}: ${r.status}`);
        const j = await r.json();
        const v = aplicarPerdida(extraerJSON(j.choices[0].message.content || ""));
        if (!Array.isArray(v.manual)) throw new Error(`${modelo}: respuesta incompleta`);
        return { v, modelo };
      } catch (e) {
        if (e.fatal) throw e;
        throw new Error(e.name === "AbortError" ? `${modelo}: tiempo agotado` : e.message);
      } finally { clearTimeout(to); senal.removeEventListener("abort", cortar); }
    };
    let ultimo = "";
    for (let i = 0; i < modelos.length; i += 2) {
      const par = modelos.slice(i, i + 2);
      log(`Revisando con el respaldo gratis (${par.join(" + ")})…`);
      try { return await carrera(par, una); } catch (e) { if (e.fatal) throw e; ultimo = e.message; }
    }
    throw new Error(`Gemini y el respaldo están saturados (${ultimo}). Intenta en unos minutos.`);
  }

  continuar();
})();
