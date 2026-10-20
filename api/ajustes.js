// GET  /api/ajustes?filial=LIMA&modulo=instalaciones  → ajustes guardados (o {} si aún no hay)
// POST /api/ajustes?filial=LIMA&modulo=instalaciones  → guarda los ajustes (JSON en el cuerpo)
// Ajustes que operaciones edita en el portal: metas (generales y por canal de atención), nombres de EECC, gestor de cada EECC
// y rutas (técnicos) que asisten cada día por EECC. Se validan antes de guardar.
const crypto = require('crypto');
const { consultar, guardar, autorizado, responder, filialDe } = require('./_supabase');

// Clave del usuario master (variable MASTER_TOKEN en Vercel): la única que puede editar agendas.
function esMaster(req) {
  const esperado = process.env.MASTER_TOKEN || '', dado = String(req.headers['x-master-token'] || '');
  if (!esperado || !dado) return false;
  const a = Buffer.from(esperado), b = Buffer.from(dado);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const MODULOS = ['instalaciones', 'averias'];
const METAS = ['meta_mes', 'meta_conv', 'meta_disp', 'meta_db', 'meta_prod', 'obj_tecnico', 'meta_cumpl'];
const texto = (v, max = 60) => String(v ?? '').replace(/[\u0000-\u001f<>]/g, '').trim().slice(0, max);
const esFecha = (k) => /^\d{4}-\d{2}-\d{2}$/.test(k);

const CANALES = ['IC', 'VI', 'OT'];   // metas por canal de atención (Integral de campo, Ventas inbound, Otros)
function limpiarMetas(m) {
  const o = {};
  for (const k of METAS) { const v = Number(m?.[k]); if (m?.[k] !== '' && m?.[k] != null && isFinite(v) && v >= 0 && v <= 1e6) o[k] = v; }
  return o;
}
function limpiar(b) {
  const o = { metas: limpiarMetas(b?.metas), metasCn: {}, nombres: {}, gestores: {}, rutas: {} };
  for (const c of CANALES) { const m = limpiarMetas(b?.metasCn?.[c]); if (Object.keys(m).length) o.metasCn[c] = m; }
  for (const campo of ['nombres', 'gestores']) {
    Object.entries(b?.[campo] || {}).slice(0, 200).forEach(([k, v]) => { const kk = texto(k, 80), vv = texto(v); if (kk && vv) o[campo][kk] = vv; });
  }
  // Rutas: solo los últimos 45 días, para que el registro no crezca sin límite.
  const fechas = Object.keys(b?.rutas || {}).filter(esFecha).sort().slice(-45);
  for (const f of fechas) {
    const dia = {};
    Object.entries(b.rutas[f] || {}).slice(0, 200).forEach(([ec, v]) => { const n = Math.round(Number(v)); const k = texto(ec, 80); if (k && isFinite(n) && n >= 0 && n <= 999) dia[k] = n; });
    if (Object.keys(dia).length) o.rutas[f] = dia;
  }
  return o;
}

// Una agenda editada a mano (tipo, F. AG y franja de una OS). null = quitar el ajuste.
function limpiarAgenda(a) {
  if (!a || typeof a !== 'object') return null;
  const t = ['AG', 'CO', 'SA'].includes(a.t) ? a.t : null; if (!t) return undefined;
  const fp = esFecha(String(a.fp || '')) ? a.fp : null; if (t !== 'SA' && !fp) return undefined;
  const fr = /^\d{1,2} - \d{1,2}$/.test(String(a.fr || '').trim()) ? String(a.fr).trim() : '';
  return { t, fp, fr, nota: texto(a.nota, 120), ab: Number.isFinite(+a.ab) ? +a.ab : null, en: new Date().toISOString() };
}
// Agendas guardadas: solo las de los últimos 120 días, para que el registro no crezca sin límite.
function podarAgendas(ag) {
  const limite = new Date(Date.now() - 120 * 864e5).toISOString().slice(0, 10), o = {};
  Object.entries(ag || {}).forEach(([k, a]) => { if (a && (!a.fp || a.fp >= limite)) o[k] = a; });
  return o;
}

// Resultado de 100 min corregido a mano para una OS (Averías). null = quitar el ajuste.
function limpiarCien(a) {
  if (!a || typeof a !== 'object') return null;
  const k = ['si', 'no', 'excl'].includes(a.k) ? a.k : null; if (!k) return undefined;
  return { k, nota: texto(a.nota, 120), ab: Number.isFinite(+a.ab) ? +a.ab : null, en: new Date().toISOString() };
}
// Resultados de 100 min corregidos: solo los de los últimos 120 días.
function podarCien(c) {
  const limite = new Date(Date.now() - 120 * 864e5).toISOString(), o = {};
  Object.entries(c || {}).forEach(([k, a]) => { if (a && (a.en || '') >= limite) o[k] = a; });
  return o;
}

// EECC asignada a mano a cada técnico (Técnicos y EECC): { "NOMBRE DEL TÉCNICO": "EECC" }. Aplica en Instalaciones y Averías.
function limpiarTecnicos(t) {
  const o = {};
  Object.entries(t && typeof t === 'object' ? t : {}).slice(0, 1500).forEach(([k, v]) => { const kk = texto(k, 100), vv = texto(v, 80); if (kk && vv) o[kk] = vv; });
  return o;
}

async function leerCuerpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  let s = '';
  for await (const c of req) { s += c; if (s.length > 600000) throw new Error('Cuerpo demasiado grande'); }
  return JSON.parse(s || '{}');
}

module.exports = async (req, res) => {
  if (!autorizado(req)) return responder(res, 401, { error: 'No autorizado' });
  const filial = filialDe(req);
  const modulo = new URL(req.url, 'http://x').searchParams.get('modulo') || 'instalaciones';
  if (!MODULOS.includes(modulo)) return responder(res, 400, { error: 'Módulo no válido' });
  const clave = `${modulo}:${filial}`;
  try {
    if (req.method === 'GET') {
      const { datos } = await consultar(`ajustes?select=datos,actualizado&clave=eq.${encodeURIComponent(clave)}`);
      return responder(res, 200, datos.length ? { ...datos[0].datos, actualizado: datos[0].actualizado } : {});
    }
    if (req.method === 'POST') {
      const cuerpo = await leerCuerpo(req);
      const { datos } = await consultar(`ajustes?select=datos&clave=eq.${encodeURIComponent(clave)}`);
      const previo = datos.length ? datos[0].datos || {} : {};
      const accion = new URL(req.url, 'http://x').searchParams.get('accion');
      let nuevo;
      if (accion === 'agenda') {
        // Editar la agenda de una OS: solo el usuario master. Se cambia solo esa OS sobre lo guardado.
        if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
        if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta: solo el usuario master puede editar agendas.' });
        const os = String(parseInt(cuerpo?.os, 10)); if (!/^\d{1,10}$/.test(os)) return responder(res, 400, { error: 'OS no válida' });
        const a = limpiarAgenda(cuerpo?.ajuste); if (a === undefined) return responder(res, 400, { error: 'Agenda no válida: indica tipo y fecha.' });
        const agendas = { ...(previo.agendas || {}) };
        if (a) agendas[os] = { ...a, por: 'master' }; else delete agendas[os];
        nuevo = { ...previo, agendas: podarAgendas(agendas) };
      } else if (accion === 'cien') {
        // Corregir el resultado de 100 min de una OS de avería: solo el usuario master.
        if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
        if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta: solo el usuario master puede modificar resultados.' });
        const os = String(parseInt(cuerpo?.os, 10)); if (!/^\d{1,10}$/.test(os)) return responder(res, 400, { error: 'OS no válida' });
        const a = limpiarCien(cuerpo?.ajuste); if (a === undefined) return responder(res, 400, { error: 'Resultado no válido.' });
        const cien = { ...(previo.cien || {}) };
        if (a) cien[os] = { ...a, por: 'master' }; else delete cien[os];
        nuevo = { ...previo, cien: podarCien(cien) };
      } else if (accion === 'tecnicos') {
        // EECC de cada técnico: solo el usuario master. Reemplaza la lista completa.
        if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
        if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta: solo el usuario master puede cambiar la EECC de los técnicos.' });
        nuevo = { ...previo, tecnicos: limpiarTecnicos(cuerpo?.tecnicos) };
      } else {
        // Metas, nombres, gestores y rutas; agendas, resultados de 100 min y técnicos guardados se conservan tal cual.
        nuevo = { ...limpiar(cuerpo), agendas: podarAgendas(previo.agendas), cien: podarCien(previo.cien), tecnicos: limpiarTecnicos(previo.tecnicos) };
      }
      const actualizado = new Date().toISOString();
      await guardar('ajustes', [{ clave, datos: nuevo, actualizado }], 'clave');
      return responder(res, 200, { ...nuevo, actualizado });
    }
    res.setHeader('Allow', 'GET, POST');
    return responder(res, 405, { error: 'Método no permitido' });
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
