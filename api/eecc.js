// /api/eecc — Portal de contratistas (EECC): cada usuario de EECC ve SOLO sus pendientes y adjunta evidencias.
//
// Usuario de EECC = usuario del portal con el campo «eecc» (lo asigna el master en Usuarios). Sus datos se filtran
// AQUÍ, en el servidor: al navegador nunca llegan los AB de otra EECC. Los teléfonos de los textos se ocultan (•••).
//
// GET  ?accion=pendientes[&eecc=X]       → instalaciones + misceláneos REGISTRADA de la EECC (eecc=X solo para el equipo interno)
// GET  ?accion=evidencias&os=N           → evidencias de esa OS (con enlace temporal para verlas)
// GET  ?accion=con_evidencia&filial=F    → { os: cantidad } de las OS con evidencia (para marcar en Pendientes)
// GET  ?accion=lista_eecc                → EECC que aparecen en los pendientes (para el selector de Usuarios)
// POST ?accion=firmar    {os, nombre, tipo, bytes}                    → enlace para subir el archivo directo a Supabase
// POST ?accion=registrar {os, ruta, nombre, tipo, bytes, comentario} → guarda la evidencia (después de subirla)
// POST ?accion=vivienda  {os, valor: 'H'|'V'|''}                     → vivienda marcada a mano por la EECC (vacío = la borra)
// GET  ?accion=comentarios&os=N          → comentarios de la EECC para esa OS (con su estado en ScordSoft)
// GET  ?accion=con_comentario&filial=F   → { os: cantidad } de las OS con comentario de EECC (marca 💬 en Pendientes)
// POST ?accion=comentar  {os, texto}                                  → comentario de la EECC (lo ve Mi Fibra y el robot lo escribe en ScordSoft)
// Las evidencias se borran solas a los 30 días (archivo y registro).
const crypto = require('crypto');
const { consultar, pedir, guardar, autorizado, responder, permitidas, puedeModulo, FILIALES } = require('./_supabase');

const URL_BASE = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const CLAVE = process.env.SUPABASE_SERVICE_KEY || '';
const BUCKET = 'evidencias';
const DIAS_EVIDENCIA = 30;
const TIPOS = {   // tipo de archivo → [extensión, tamaño máximo]
  'image/jpeg': ['jpg', 10e6], 'image/png': ['png', 10e6], 'image/webp': ['webp', 10e6],
  'application/pdf': ['pdf', 10e6],
  'video/mp4': ['mp4', 50e6], 'video/quicktime': ['mov', 50e6], 'video/webm': ['webm', 50e6],
};
const filialesReales = (f) => permitidas(f).filter(x => x !== 'PROVINCIAS');   // las 7 provincias se leen una por una
// Teléfonos (celular 9xx xxx xxx, con o sin +51, y números de 7 o más cifras) → •••. Solo en los textos libres:
// coordenadas, códigos y fechas no se tocan.
const sinTel = (v) => typeof v === 'string' ? v.replace(/(?:\+?51[\s-]?)?(?<![\d.,])9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\d.])|(?<![\d.,-])\d{7,}(?![\d.])/g, '•••') : v;
const TEXTOS = ['ov', 'ovt', 'ove', 'vi', 'fr', 'plano'];
const limpiar = (x) => { const o = { ...x }; TEXTOS.forEach(k => { if (typeof o[k] === 'string') o[k] = sinTel(o[k]); }); delete o.tel; delete o.cel; return o; };
const carpeta = (f) => String(f).replace(/\s+/g, '_');
const texto = (v, max = 300) => String(v ?? '').replace(/[\u0000-\u001f<>]/g, ' ').trim().slice(0, max);

async function storage(ruta, method, cuerpo) {
  const r = await fetch(`${URL_BASE}/storage/v1/${ruta}`, { method, headers: { apikey: CLAVE, Authorization: `Bearer ${CLAVE}`, 'Content-Type': 'application/json' },
    body: cuerpo == null ? undefined : JSON.stringify(cuerpo) });
  const t = await r.text();
  if (!r.ok) throw new Error(`Almacenamiento ${r.status}: ${t.slice(0, 200)}`);
  try { return JSON.parse(t); } catch { return t; }
}

// Pendientes de las filiales con la EECC tal como la ve el portal: técnico reasignado a otra EECC (Técnicos y EECC),
// nombre corregido (Nombres de EECC), gestor de la EECC y agenda editada a mano. Misceláneos = los mismos de la pestaña
// Pendientes de Instalaciones (pendx: los tipos de trabajo marcados para instalaciones en TIPO_AB).
async function pendientesPortal(filiales) {
  const cl = filiales.map(f => encodeURIComponent('instalaciones:' + f)).join(',');
  const [{ datos: T }, { datos: A }] = await Promise.all([
    consultar(`tableros?select=clave,datos->pend,datos->pendx,datos->avt,datos->corte,actualizado&clave=in.(${cl})`),
    consultar(`ajustes?select=clave,datos&clave=in.(${cl})`)]);
  const aj = {}; A.forEach(x => { aj[x.clave] = x.datos || {}; });
  const L = [], H = []; let actualizado = null;   // H = órdenes descargadas HOY por técnicos de EECC (avance del día)
  for (const t of T) {
    const filial = t.clave.split(':')[1], a = aj[t.clave] || {};
    const tecs = a.tecnicos || {}, noms = a.nombres || {}, ges = a.gestores || {}, ags = a.agendas || {};
    if (!actualizado || t.actualizado > actualizado) actualizado = t.actualizado;
    const fijar = (p, grupo) => { const ec0 = (p.tec && tecs[p.tec]) || p.ec, ag = ags[p.os];
      const x = { ...p, filial, grupo, to: p.to || 'INSTALACION SERVICIO', ec0, ec: noms[ec0] || ec0, ge: ges[ec0] || null };
      if (grupo === 'INSTALACION' && ag) Object.assign(x, { t: ag.t, fp: ag.fp || null, fr: ag.fr || x.fr, fo: 'aj' });
      delete x.cj; return x; };
    (t.pend || []).forEach(p => L.push(fijar(p, 'INSTALACION')));
    (t.pendx || []).forEach(p => L.push(fijar(p, 'MISCELANEO')));
    // Avance del día: misma EECC que ve el portal (técnico reasignado a mano y nombre corregido).
    const A = t.avt, dia = t.corte ? parseInt(String(t.corte).slice(8, 10), 10) : 0;
    if (A && A.o && dia) A.o.forEach(x => { if (x[0] !== dia) return;
      const tec = A.k[x[4]], ec0 = (tec && tecs[tec]) || A.e[x[3]];
      H.push({ to: A.t[x[1]], sv: (A.s || [])[x[2]] || '', tec, ec0, ec: noms[ec0] || ec0, os: x[5], ab: x[6], hi: x[7] || '', hf: x[8] || '', di: (A.di || [])[x[9]] || '', filial }); });
  }
  return { L, H, actualizado };
}
const CACHE = new Map();   // 60 s por instancia (el robot actualiza cada 20 min)
async function pendientesDe(eecc, filiales) {
  const k = eecc + '|' + filiales.join(','), c = CACHE.get(k);
  if (c && Date.now() - c.en < 60e3) return c.d;
  const { L, H, actualizado } = await pendientesPortal(filiales);
  const mia = (x) => x.ec === eecc || x.ec0 === eecc, h = H.filter(mia);
  // Hoy: instalaciones liquidadas (descargadas) y rutas en campo = técnicos con al menos 1 orden liquidada hoy.
  // Instalaciones = solo servicio INTERNET (igual que Instalaciones › Avance); las de SERVICIO OTT no cuentan.
  const ins = h.filter(x => x.to === 'INSTALACION SERVICIO' && (!x.sv || x.sv === 'INTERNET' || x.sv === 'SIN SERVICIO'));
  const hoy = { liq: ins.length, rutas: new Set(h.map(x => x.tec).filter(Boolean)).size,
    det: ins.map(x => ({ ab: x.ab, os: x.os, tec: x.tec, di: x.di, hi: x.hi, hf: x.hf, filial: x.filial })).sort((a, b) => String(a.hf).localeCompare(String(b.hf))) };
  const d = { pend: L.filter(mia).map(limpiar), hoy, actualizado };
  CACHE.set(k, { en: Date.now(), d });
  return d;
}
async function listaEecc() {
  const { L } = await pendientesPortal(FILIALES.filter(f => f !== 'PROVINCIAS'));
  const n = {}; L.forEach(p => { if (p.ec && !/^(POR ASIGNAR|SIN EECC|SUPERVISOR)$/.test(p.ec)) n[p.ec] = (n[p.ec] || 0) + 1; });
  return Object.keys(n).sort((a, b) => n[b] - n[a]).map(k => ({ eecc: k, pendientes: n[k] }));
}

// Borrado automático a los 30 días (se hace de vez en cuando, al subir o listar)
async function podar() {
  const limite = new Date(Date.now() - DIAS_EVIDENCIA * 864e5).toISOString();
  const { datos } = await consultar(`evidencias?select=id,ruta&creado_en=lt.${limite}&limit=500`);
  if (!datos.length) return;
  await storage(`object/${BUCKET}`, 'DELETE', { prefixes: datos.map(x => x.ruta) });
  await pedir(`evidencias?id=in.(${datos.map(x => x.id).join(',')})`, 'DELETE');
}

// Comentarios de la EECC: los de la casilla y los que acompañan a una evidencia (el mismo texto subido con
// varios archivos se guarda una sola vez).
const COLS_COM = 'id,os,ab,eecc,usuario,nombre_usuario,texto,origen,creado_en,scord_estado,scord_en';
async function nuevoComentario(p, eecc, u, txt, origen) {
  const desde = new Date(Date.now() - 15 * 60e3).toISOString();
  const { datos } = await consultar(`eecc_comentarios?select=id&os=eq.${p.os}&texto=eq.${encodeURIComponent(txt)}&creado_en=gte.${desde}&limit=1`);
  if (datos.length) return false;
  await pedir('eecc_comentarios', 'POST', [{ os: p.os, ab: p.ab || null, filial: p.filial, eecc, usuario: u.u, nombre_usuario: u.n || u.u, texto: txt, origen }]);
  return true;
}

async function leerCuerpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  let s = ''; for await (const c of req) { s += c; if (s.length > 20000) throw new Error('Cuerpo demasiado grande'); }
  return JSON.parse(s || '{}');
}

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const u = req.usuario || {}, q = new URL(req.url, 'http://x').searchParams, accion = q.get('accion') || 'pendientes';
  const esEecc = !!u.e, interno = !u.e && puedeModulo(req, 'instalaciones');
  if (!esEecc && !interno) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  // EECC: la suya (no se puede cambiar). Equipo interno: puede mirar la vista de una EECC con ?eecc=X.
  const eecc = esEecc ? u.e : texto(q.get('eecc') || '', 80).toUpperCase();
  const filiales = filialesReales(u.f || 'TODAS');
  try {
    if (req.method === 'GET' && accion === 'lista_eecc') {
      if (!interno) return responder(res, 403, { error: 'Solo para el equipo interno.' });
      return responder(res, 200, { eecc: await listaEecc() });
    }
    if (req.method === 'GET' && accion === 'pendientes') {
      if (!eecc) return responder(res, 400, { error: 'Elige una EECC.' });
      const d = await pendientesDe(eecc, filiales);
      const os = d.pend.map(p => p.os);
      const ev = {}, vm = {};
      if (os.length) {
        const [{ datos }, { datos: V }] = await Promise.all([consultar(`evidencias?select=os&os=in.(${os.join(',')})`),
          consultar(`eecc_vivienda?select=os,valor&os=in.(${os.join(',')})`).catch(() => ({ datos: [] }))]);
        datos.forEach(x => { ev[x.os] = (ev[x.os] || 0) + 1; });
        V.forEach(x => { vm[x.os] = x.valor; });
      }
      return responder(res, 200, { eecc, filiales, actualizado: d.actualizado, pend: d.pend, hoy: d.hoy, evidencias: ev, vivienda: vm });
    }
    if (req.method === 'GET' && accion === 'con_evidencia') {
      if (!interno) return responder(res, 403, { error: 'Solo para el equipo interno.' });
      const pedidas = String(q.get('filial') || '').toUpperCase().split(',').flatMap(f => f === 'PROVINCIAS' ? filialesReales('PROVINCIAS') : [f]);
      const fs = pedidas.filter(f => filiales.includes(f)); if (!fs.length) return responder(res, 200, { evidencias: {} });
      const { datos } = await consultar(`evidencias?select=os&filial=in.(${fs.map(encodeURIComponent).join(',')})`);
      const ev = {}; datos.forEach(x => { ev[x.os] = (ev[x.os] || 0) + 1; });
      return responder(res, 200, { evidencias: ev });
    }
    if (req.method === 'GET' && accion === 'con_comentario') {
      if (!interno) return responder(res, 403, { error: 'Solo para el equipo interno.' });
      const f0 = String(q.get('filial') || '').toUpperCase();
      const pedidas = f0 === 'TODAS' ? filiales : f0.split(',').flatMap(f => f === 'PROVINCIAS' ? filialesReales('PROVINCIAS') : [f]);
      const fs = pedidas.filter(f => filiales.includes(f)); if (!fs.length) return responder(res, 200, { comentarios: {} });
      const desde = new Date(Date.now() - 60 * 864e5).toISOString();
      const { datos } = await consultar(`eecc_comentarios?select=os&filial=in.(${fs.map(encodeURIComponent).join(',')})&creado_en=gte.${desde}`);
      const c = {}; datos.forEach(x => { c[x.os] = (c[x.os] || 0) + 1; });
      return responder(res, 200, { comentarios: c }, 60);
    }
    if (req.method === 'GET' && accion === 'comentarios') {
      const os = parseInt(q.get('os'), 10); if (!os) return responder(res, 400, { error: 'OS no válida' });
      if (esEecc && !(await pendientesDe(eecc, filiales)).pend.some(p => p.os === os)) return responder(res, 403, { error: 'Esa OS no es de tu EECC.' });
      const { datos } = await consultar(`eecc_comentarios?select=${COLS_COM}&os=eq.${os}&order=creado_en.desc&limit=50`);
      return responder(res, 200, { comentarios: datos.map(x => ({ ...x, texto: sinTel(x.texto) })) });
    }
    if (req.method === 'POST' && accion === 'comentar') {
      if (!esEecc) return responder(res, 403, { error: 'Solo los usuarios de EECC escriben aquí.' });
      const b = await leerCuerpo(req), os = parseInt(b.os, 10), txt = texto(b.texto, 500);
      if (txt.length < 3) return responder(res, 400, { error: 'Escribe el comentario.' });
      const p = (await pendientesDe(eecc, filiales)).pend.find(x => x.os === os);
      if (!p) return responder(res, 403, { error: 'Esa OS no es de tu EECC o ya no está pendiente.' });
      const nuevo = await nuevoComentario(p, eecc, u, txt, 'casilla');
      return responder(res, 200, { ok: true, repetido: !nuevo });
    }
    if (req.method === 'GET' && accion === 'evidencias') {
      const os = parseInt(q.get('os'), 10); if (!os) return responder(res, 400, { error: 'OS no válida' });
      if (esEecc && !(await pendientesDe(eecc, filiales)).pend.some(p => p.os === os)) return responder(res, 403, { error: 'Esa OS no es de tu EECC.' });
      const { datos } = await consultar(`evidencias?select=id,os,ab,eecc,usuario,nombre_usuario,nombre,tipo,bytes,comentario,ruta,creado_en&os=eq.${os}&order=creado_en.desc`);
      let firmas = [];
      if (datos.length) firmas = await storage(`object/sign/${BUCKET}`, 'POST', { expiresIn: 3600, paths: datos.map(x => x.ruta) });
      const url = {}; (firmas || []).forEach(f => { if (f.signedURL) url[f.path] = `${URL_BASE}/storage/v1${f.signedURL}`; });
      if (Math.random() < 0.1) podar().catch(() => {});
      return responder(res, 200, { evidencias: datos.map(x => ({ ...x, url: url[x.ruta] || null, vence: new Date(new Date(x.creado_en).getTime() + DIAS_EVIDENCIA * 864e5).toISOString() })) });
    }
    if (req.method === 'POST' && accion === 'vivienda') {
      if (!esEecc) return responder(res, 403, { error: 'Solo los usuarios de EECC marcan la vivienda.' });
      const b = await leerCuerpo(req), os = parseInt(b.os, 10), valor = String(b.valor || '').toUpperCase();
      if (!['H', 'V', ''].includes(valor)) return responder(res, 400, { error: 'Vivienda: H (horizontal) o V (vertical).' });
      if (!(await pendientesDe(eecc, filiales)).pend.some(p => p.os === os)) return responder(res, 403, { error: 'Esa OS no es de tu EECC o ya no está pendiente.' });
      if (valor) await guardar('eecc_vivienda', [{ os, valor, eecc, usuario: u.u, actualizado: new Date().toISOString() }], 'os');
      else await pedir(`eecc_vivienda?os=eq.${os}`, 'DELETE');
      return responder(res, 200, { ok: true, os, valor });
    }
    if (req.method === 'POST' && (accion === 'firmar' || accion === 'registrar')) {
      if (!esEecc) return responder(res, 403, { error: 'Solo los usuarios de EECC adjuntan evidencias.' });
      const b = await leerCuerpo(req), os = parseInt(b.os, 10), tipo = String(b.tipo || ''), bytes = Number(b.bytes) || 0;
      const p = (await pendientesDe(eecc, filiales)).pend.find(x => x.os === os);
      if (!p) return responder(res, 403, { error: 'Esa OS no es de tu EECC o ya no está pendiente.' });
      const t = TIPOS[tipo]; if (!t) return responder(res, 400, { error: 'Tipo de archivo no permitido (fotos JPG/PNG, PDF o video MP4/MOV/WEBM).' });
      if (!bytes || bytes > t[1]) return responder(res, 400, { error: `El archivo pesa más de ${t[1] / 1e6} MB.` });
      if (accion === 'firmar') {
        const ruta = `${carpeta(p.filial)}/${os}/${Date.now()}-${crypto.randomBytes(4).toString('hex')}.${t[0]}`;
        const s = await storage(`object/upload/sign/${BUCKET}/${ruta}`, 'POST', {});
        return responder(res, 200, { ruta, subida: `${URL_BASE}/storage/v1${s.url}` });
      }
      const ruta = String(b.ruta || '');
      if (!new RegExp(`^${carpeta(p.filial)}/${os}/\\d+-[0-9a-f]{8}\\.${t[0]}$`).test(ruta)) return responder(res, 400, { error: 'Ruta de archivo no válida.' });
      await pedir('evidencias', 'POST', [{ os, ab: p.ab || null, filial: p.filial, eecc, usuario: u.u, nombre_usuario: u.n || u.u, nombre: texto(b.nombre, 120) || 'archivo',
        tipo, bytes, comentario: texto(b.comentario, 500) || null, ruta }]);
      const com = texto(b.comentario, 500); if (com.length >= 3) await nuevoComentario(p, eecc, u, com, 'evidencia').catch(() => {});
      CACHE.clear();
      if (Math.random() < 0.2) podar().catch(() => {});
      return responder(res, 200, { ok: true });
    }
    return responder(res, 400, { error: 'Acción no válida' });
  } catch (e) {
    return responder(res, 500, { error: e.message });
  }
};
module.exports.listaEecc = listaEecc;   // también la usa /api/usuarios (selector de EECC del master)
