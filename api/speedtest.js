// GET /api/speedtest
// Tablero de Speed Test (todas las filiales): lo calcula el robot en cada sincronización con los enlaces de
// «Observaciones Tecnico» y lo guarda en la tabla "tableros" (clave "speedtest"). La página filtra en el navegador.
// POST /api/speedtest?accion=vel (solo el robot, con firma): respaldo para leer velocidades en speedtest.net desde Vercel
// cuando speedtest.net no responde a los servidores de GitHub. Cuerpo {claves: [...]} → {r: {clave: [bajada, subida, red Mi Fibra 1/0, fecha] | [-1] | null}}.
const crypto = require('crypto');
const zlib = require('zlib');
const { consultar, leerTableros, autorizado, responder, puedeVer, permitidas, puedeModulo, guardar } = require('./_supabase');

// Firma del robot: HMAC-SHA256 con la clave de Supabase (la tienen el robot y Vercel) sobre «speedtest-vel:AAAA-MM-DD» (hoy o ayer, UTC)
function firmaOk(req) {
  const k = process.env.SUPABASE_SERVICE_KEY || '', f = String(req.headers['x-robot-firma'] || '');
  if (!k || f.length !== 64) return false;
  return [0, 1].some(d => { const e = crypto.createHmac('sha256', k).update('speedtest-vel:' + new Date(Date.now() - d * 864e5).toISOString().slice(0, 10)).digest('hex');
    return crypto.timingSafeEqual(Buffer.from(e), Buffer.from(f)); });
}
async function leerVel(clave) {   // igual que el robot: /api/result/… de speedtest.net
  const m = /^speedtest\.net\/(?:my-)?(result\/[a-z0-9/_-]+)$/.exec(clave); if (!m) return [-1];
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 7000);
  try {
    const r = await fetch('https://www.speedtest.net/api/' + m[1], { signal: ctl.signal, headers: { Accept: 'application/json', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36' } });
    if (r.status === 404) return [-1]; if (!r.ok) return null;
    const j = await r.json(), isp = `${j.isp_name || ''} ${j.display_provider_name || ''}`.toLowerCase();
    // Todo lo público de la prueba (lo mismo que muestra su página): conexión w=Wi-Fi e=cable c=celular, modo m=multi s=una, pings, servidor
    const ic = { wireless: 'w', ethernet: 'e', cell: 'c', cellular: 'c' }[String(j.connection_icon || '').toLowerCase()] ?? String(j.connection_icon || '').slice(0, 8);
    const md = { multi: 'm', single: 's' }[String(j.connection_mode || '').toLowerCase()] || '', nm = (x) => (x == null || isNaN(+x) ? null : Math.round(+x * 10) / 10);
    return [Math.round((j.download || 0) / 1000), Math.round((j.upload || 0) / 1000), isp.includes('fibra') ? 1 : 0, +j.date || 0,
      ic, md, nm(j.latency), nm(j.idle_latency), nm(j.download_latency), nm(j.upload_latency), String(j.server_name || '').slice(0, 40), String(j.sponsor_name || '').slice(0, 40)];
  } catch { return null; } finally { clearTimeout(t); }
}
async function cuerpo(req) { if (req.body && typeof req.body === 'object') return req.body; let s = ''; for await (const c of req) { s += c; if (s.length > 20000) break; } return JSON.parse(s || '{}'); }

// El tablero (≈1 MB) se guarda en memoria 2 min por instancia: no se vuelve a pedir a Supabase en cada visita.
let MEM = { en: 0, d: null };
async function tablero() {
  if (MEM.d && Date.now() - MEM.en < 20e3) return MEM.d;   // ráfagas de pedidos: 20 s; luego se revisa la hora (memoria por actualización)
  const { datos } = await leerTableros('speedtest', 'datos,actualizado');
  MEM = { en: Date.now(), d: datos[0] || null }; return MEM.d;
}

/* ---------- POST /api/speedtest?accion=control · datos del Control Operativo SpeedTest (envío automático diario) ----------
   Lo llama el proceso diario del Control Operativo con la cabecera «x-clave-control» (= variable CONTROL_ST_TOKEN de Vercel).
   Cuerpo: JSON {ordenes: "<ordenes.csv>", pruebas: "<pruebas.csv>", meta: {...}} — puede ir comprimido en gzip
   (Content-Encoding: gzip o Content-Type: application/gzip). Se guarda, por instalación de los 2 últimos meses,
   «FILIAL|N.º OS» → [[quién C/T, bajada, subida, estado v/b/e/n]] en ajustes («speedtest:control»); el robot lo usa como
   archivo de control en su siguiente pasada (manda sobre los enlaces). */
function claveControlOk(req) {
  const k = process.env.CONTROL_ST_TOKEN || '', f = String(req.headers['x-clave-control'] || '');
  if (k.length < 16 || f.length !== k.length) return false;
  return crypto.timingSafeEqual(Buffer.from(k), Buffer.from(f));
}
async function cuerpoGrande(req) {
  let buf;
  if (Buffer.isBuffer(req.body)) buf = req.body;
  else if (req.body && typeof req.body === 'object') return req.body;
  else if (typeof req.body === 'string') buf = Buffer.from(req.body);
  else { const P = []; let n = 0; for await (const c of req) { n += c.length; if (n > 4.4e6) throw new Error('Envío demasiado grande (máx. 4,4 MB comprimido)'); P.push(c); } buf = Buffer.concat(P); }
  if (buf.length > 1 && buf[0] === 0x1f && buf[1] === 0x8b) buf = zlib.gunzipSync(buf, { maxOutputLength: 80e6 });
  return JSON.parse(buf.toString('utf8') || '{}');
}
function leerCsv(t) {   // CSV con comillas (los nombres traen coma): → lista de objetos por encabezado
  t = String(t || '').replace(/^\uFEFF/, ''); const filas = []; let f = [], c = '', q = false;
  for (let i = 0; i < t.length; i++) { const ch = t[i];
    if (q) { if (ch === '"') { if (t[i + 1] === '"') { c += '"'; i++; } else q = false; } else c += ch; }
    else if (ch === '"') q = true; else if (ch === ',') { f.push(c); c = ''; } else if (ch === '\n' || ch === '\r') { if (ch === '\r' && t[i + 1] === '\n') i++; f.push(c); c = ''; if (f.length > 1 || f[0]) filas.push(f); f = []; } else c += ch; }
  if (c || f.length) { f.push(c); filas.push(f); }
  const cab = (filas.shift() || []).map(x => x.trim()); return filas.map(r => Object.fromEntries(cab.map((k, i) => [k, r[i] ?? ''])));
}
async function recibirControl(req, res) {
  if (!claveControlOk(req)) return responder(res, 401, { error: 'No autorizado' });
  try {
    const b = await cuerpoGrande(req), O = leerCsv(b.ordenes), T = leerCsv(b.pruebas);
    if (!O.length || !('id_orden' in O[0]) || !('nro_orden' in O[0])) return responder(res, 400, { error: 'Falta ordenes.csv (con id_orden, filial, nro_orden, mes, tipo).' });
    if (T.length && !('equipo' in T[0])) return responder(res, 400, { error: 'pruebas.csv sin la columna equipo.' });
    const EST = { 'Válida': 'v', 'La red no entregó': 'b', 'Equipo del técnico insuficiente': 'e', 'Prueba no certificable': 'n' };
    const meses = [...new Set(O.map(o => o.mes))].sort().slice(-2);   // como el robot: 2 meses
    const tb = {}; T.forEach(t => { if (t.tipo === 'Instalación') (tb[t.id_orden] = tb[t.id_orden] || []).push(t); });
    const o = {}; let n = 0, desde = '', hasta = '';
    O.forEach(r => { if (r.tipo !== 'Instalación' || !meses.includes(r.mes)) return; const k = `${r.filial}|${parseInt(r.nro_orden, 10)}`;
      o[k] = (tb[r.id_orden] || []).sort((a, z) => String(a.fecha_hora_lima).localeCompare(String(z.fecha_hora_lima)))
        .map(t => [t.equipo === 'Cliente' ? 'C' : 'T', Math.round(+t.descarga_mbps || 0), Math.round(+t.subida_mbps || 0), EST[t.resultado_origen] || (+t.descarga_mbps > +r.umbral_mbps ? 'v' : 'n')]);
      n++; if (!desde || r.fecha_atencion < desde) desde = r.fecha_atencion; if (r.fecha_atencion > hasta) hasta = r.fecha_atencion; });
    const meta = b.meta && typeof b.meta === 'object' ? b.meta : {};
    const datos = { fuente: `Control Operativo SpeedTest · envío automático · instalaciones del ${desde} al ${hasta}${meta.generado ? ' · generado ' + meta.generado : ''}`, recibido: new Date().toISOString(), meta, o };
    await guardar('ajustes', [{ clave: 'speedtest:control', datos, actualizado: datos.recibido }], 'clave');
    return responder(res, 200, { ok: true, instalaciones: n, desde, hasta, pruebas: T.length });
  } catch (e) { return responder(res, 400, { error: String(e.message || e).slice(0, 300) }); }
}

module.exports = async (req, res) => {
  if (req.method === 'POST' && new URL(req.url, 'http://x').searchParams.get('accion') === 'control') return recibirControl(req, res);
  if (req.method === 'POST' && new URL(req.url, 'http://x').searchParams.get('accion') === 'vel') {
    if (!firmaOk(req)) return responder(res, 401, { error: 'No autorizado' });
    try {
      const b = await cuerpo(req), C = [...new Set((Array.isArray(b.claves) ? b.claves : []).map(String).filter(c => c.startsWith('speedtest.net/')))].slice(0, 40), r = {};
      for (let i = 0; i < C.length; i += 8) { const lote = C.slice(i, i + 8), v = await Promise.all(lote.map(leerVel)); lote.forEach((c, k) => { r[c] = v[k]; }); }
      return responder(res, 200, { r });
    } catch (e) { return responder(res, 500, { error: e.message }); }
  }
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'speedtest')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  try {
    // ?os=1,2,3: datos de esas OS de instalación para las listas de AB (F. Recep, F. atención, distrito y estado)
    const os = new URL(req.url, 'http://x').searchParams.get('os');
    if (os != null) {
      const L = [...new Set(os.split(',').map(v => parseInt(v, 10)).filter(v => v > 0))].slice(0, 300);
      if (!L.length) return responder(res, 200, []);
      const { datos } = await consultar(`ordenes_servicio?select=nro_os,abonado_codigo,filial,fecha_registro,fecha_atencion,distrito,estado_os&tipo_orden=eq.${encodeURIComponent('INSTALACION SERVICIO')}&nro_os=in.(${L.join(',')})${req.usuario.f !== 'TODAS' ? `&filial=in.(${permitidas(req.usuario.f).map(f => `"${f}"`).join(',')})` : ''}`);
      return responder(res, 200, datos);
    }
    const t = await tablero();
    if (!t) return responder(res, 404, { error: 'Aún no hay datos de Speed Test: aparecen después de la próxima actualización del robot.' });
    let d = t.datos;
    if (req.usuario.f !== 'TODAS') {   // usuario de una filial: solo sus filas (y los enlaces repetidos re-numerados)
      const nuevo = new Map(), o = [];
      d.o.forEach((f, i) => { if (puedeVer(req, d.f[f[0]])) { nuevo.set(i, o.length); o.push(f); } });
      const rep = (d.rep || []).map(([u, ix]) => [u, ix.filter(i => nuevo.has(i)).map(i => nuevo.get(i))]).filter(r => r[1].length);
      d = { ...d, o, rep };
    }
    responder(res, 200, { ...d, actualizado: t.actualizado }, 120);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
