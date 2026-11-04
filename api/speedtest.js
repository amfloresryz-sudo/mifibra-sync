// GET /api/speedtest
// Tablero de Speed Test (todas las filiales): lo calcula el robot en cada sincronización con los enlaces de
// «Observaciones Tecnico» y lo guarda en la tabla "tableros" (clave "speedtest"). La página filtra en el navegador.
// POST /api/speedtest?accion=vel (solo el robot, con firma): respaldo para leer velocidades en speedtest.net desde Vercel
// cuando speedtest.net no responde a los servidores de GitHub. Cuerpo {claves: [...]} → {r: {clave: [bajada, subida, red Mi Fibra 1/0, fecha] | [-1] | null}}.
const crypto = require('crypto');
const { consultar, autorizado, responder, puedeVer, permitidas, puedeModulo } = require('./_supabase');

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
  if (MEM.d && Date.now() - MEM.en < 120e3) return MEM.d;
  const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.speedtest');
  MEM = { en: Date.now(), d: datos[0] || null }; return MEM.d;
}

module.exports = async (req, res) => {
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
