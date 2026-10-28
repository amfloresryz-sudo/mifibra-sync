// GET  /api/zonificacion  → todos los distritos con sus planos (tablero que arma el robot) + EECC editadas en el portal (al instante)
// POST /api/zonificacion  → { cambios: { "FILIAL|DISTRITO|PLANO": "EECC" } } guarda la EECC de esos planos ("" = vuelve a la de la hoja ZONA).
//                           Solo el usuario master (clave master en x-master-token).
const { consultar, guardar, autorizado, responder, puedeVer, puedeModulo, esMaster } = require('./_supabase');

const CLAVE_EDIT = 'zonificacion:GLOBAL';
let MEM = { en: 0, d: null };
async function tablero() {
  if (MEM.d && Date.now() - MEM.en < 120e3) return MEM.d;
  const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.zonificacion');
  MEM = { en: Date.now(), d: datos[0] || null }; return MEM.d;
}
async function editadas() {
  const { datos } = await consultar(`ajustes?select=datos,actualizado&clave=eq.${encodeURIComponent(CLAVE_EDIT)}`);
  return datos.length ? (datos[0].datos || {}) : {};
}
async function leerCuerpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  let s = ''; for await (const c of req) { s += c; if (s.length > 300000) throw new Error('Cuerpo demasiado grande'); }
  return JSON.parse(s || '{}');
}
const txt = (v, n) => String(v ?? '').replace(/[\u0000-\u001f<>]/g, '').trim().toUpperCase().slice(0, n);

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  // ?areas=1 → áreas de los planos (polígonos de Google My Maps) para el mapa de Ruteo de Pendientes
  if (new URL(req.url, 'http://x').searchParams.get('areas')) {
    if (!puedeModulo(req, 'instalaciones', 'zonificacion', 'fftt')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
    try { const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.areas');
      return responder(res, 200, datos.length ? { ...datos[0].datos, actualizado: datos[0].actualizado } : { areas: [] }, datos.length ? 300 : 0); }   // sin áreas no se guarda en caché
    catch (e) { return responder(res, 500, { error: e.message }); }
  }
  if (!puedeModulo(req, 'zonificacion', 'fftt')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  try {
    if (req.method === 'POST') {
      if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
      if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta: solo el usuario master puede cambiar la EECC de los planos.' });
      const cuerpo = await leerCuerpo(req), previo = await editadas(), eecc = { ...(previo.eecc || {}) };
      Object.entries(cuerpo?.cambios || {}).slice(0, 3000).forEach(([k, v]) => { const kk = txt(k, 200); if (!/^[^|]+\|[^|]*\|[^|]+$/.test(kk)) return; const e = txt(v, 60); if (e) eecc[kk] = e; else delete eecc[kk]; });
      const actualizado = new Date().toISOString(), datos = { eecc, por: 'master' };
      await guardar('ajustes', [{ clave: CLAVE_EDIT, datos, actualizado }], 'clave');
      return responder(res, 200, { eecc, actualizado });
    }
    const [t, ed] = await Promise.all([tablero(), editadas()]);
    if (!t) return responder(res, 404, { error: 'Aún no hay datos de Zonificación: aparecen después de la próxima actualización del robot.' });
    const d = t.datos;
    const filas = req.usuario && req.usuario.f && req.usuario.f !== 'TODAS' ? d.filas.filter(f => puedeVer(req, f[0])) : d.filas;
    responder(res, 200, { ...d, filas, eecc: ed.eecc || {}, actualizado: t.actualizado }, 0);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
