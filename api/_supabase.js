// Cliente mínimo de la API REST de Supabase. Solo se usa en el servidor (funciones de Vercel):
// la service_role key vive en las variables de entorno de Vercel y nunca llega al navegador.

const URL_BASE = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const CLAVE = process.env.SUPABASE_SERVICE_KEY || '';

async function consultar(ruta, { contar = false } = {}) {
  if (!URL_BASE || !CLAVE) throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en Vercel');
  const headers = { apikey: CLAVE, Authorization: `Bearer ${CLAVE}` };
  if (contar) headers.Prefer = 'count=exact';
  const r = await fetch(`${URL_BASE}/rest/v1/${ruta}`, { headers });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const datos = await r.json();
  const rango = r.headers.get('content-range'); // ej. "0-49/1752"
  const total = rango && rango.includes('/') ? Number(rango.split('/')[1]) : undefined;
  return { datos, total };
}

// Tableros con memoria (por instancia de Vercel): primero se pide solo la hora de cada tablero (consulta de bytes) y,
// si no cambió desde la última lectura, se responde con la copia en memoria sin volver a bajar el tablero.
// Los tableros grandes ya no viven dentro de la base: el robot los guarda comprimidos como archivo en Supabase Storage
// (bucket «tableros») y en la tabla deja solo un puntero {_st: ruta, _v: versión}. Así la base casi no escribe en disco
// (el disco del plan gratuito se agotaba) y lo que baja es ~5 veces más chico. Los tableros chicos siguen en la tabla.
const zlib = require('zlib');
const MEM_TAB = new Map();   // clave → { act, datos }
async function datosDe(fila) {
  const d = fila.datos;
  if (!d || typeof d !== 'object' || !d._st) return d;
  const r = await fetch(`${URL_BASE}/storage/v1/object/tableros/${d._st}?v=${encodeURIComponent(d._v || '')}`,
    { headers: { apikey: CLAVE, Authorization: `Bearer ${CLAVE}` }, cache: 'no-store' });
  if (!r.ok) throw new Error(`Storage ${r.status} al leer el tablero ${fila.clave}`);
  return JSON.parse(zlib.gunzipSync(Buffer.from(await r.arrayBuffer())).toString('utf8'));
}
async function leerTableros(claves, select = 'clave,datos,actualizado') {
  const lista = [].concat(claves), enc = lista.map(c => encodeURIComponent(c)).join(',');
  const { datos: H } = await consultar(`tableros?select=clave,actualizado&clave=in.(${enc})`);
  const falta = H.filter(x => { const m = MEM_TAB.get(x.clave); return !m || m.act !== x.actualizado; }).map(x => x.clave);
  if (falta.length) {
    const { datos: F } = await consultar(`tableros?select=clave,datos,actualizado&clave=in.(${falta.map(c => encodeURIComponent(c)).join(',')})`);
    const listos = await Promise.all(F.map(async x => [x.clave, { act: x.actualizado, datos: await datosDe(x) }]));
    listos.forEach(([k, v]) => { MEM_TAB.delete(k); MEM_TAB.set(k, v); });
    while (MEM_TAB.size > 40) MEM_TAB.delete(MEM_TAB.keys().next().value);   // las más viejas salen primero
  }
  // Mismas columnas que pedía la consulta original: clave, actualizado, datos o datos->campo (sale como «campo»)
  const cols = select.split(',').map(c => c.trim());
  const filas = H.filter(x => MEM_TAB.has(x.clave)).map(x => {
    const m = MEM_TAB.get(x.clave), o = {};
    cols.forEach(c => {
      if (c === 'clave') o.clave = x.clave;
      else if (c === 'actualizado') o.actualizado = m.act;
      else if (c === 'datos') o.datos = m.datos;
      else if (c.startsWith('datos->')) { const k = c.slice(7); o[k] = m.datos && m.datos[k] !== undefined ? m.datos[k] : null; }
    });
    return o;
  });
  return { datos: structuredClone(filas), mem: !falta.length };
}

// Inserta o reemplaza filas (upsert) en una tabla. Solo lo usan los ajustes del portal.
async function guardar(tabla, filas, conflicto) {
  if (!URL_BASE || !CLAVE) throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en Vercel');
  const r = await fetch(`${URL_BASE}/rest/v1/${tabla}?on_conflict=${conflicto}`, {
    method: 'POST',
    headers: { apikey: CLAVE, Authorization: `Bearer ${CLAVE}`, 'Content-Type': 'application/json', Prefer: 'resolution=merge-duplicates,return=minimal' },
    body: JSON.stringify(filas),
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 300)}`);
}

// ---------- Acceso: usuarios propios (tabla "usuarios") y, mientras siga activa, la clave general (PORTAL_TOKEN) ----------
// La sesión de un usuario es un token firmado (no se consulta la base en cada pedido): u1.<datos>.<firma>.
// Lleva usuario, nombre, filial permitida y vencimiento (12 h). Desactivar, cambiar filial o restablecer la clave lo invalida
// al instante gracias a la lista "rev" guardada en ajustes (acceso:GLOBAL), que se lee como máximo una vez por minuto.
const crypto = require('crypto');
const DURACION_SESION = 12 * 3600e3;
const secreto = () => process.env.SESION_SECRET || process.env.MASTER_TOKEN || process.env.PORTAL_TOKEN || '';
const firma = (c) => crypto.createHmac('sha256', 'mf-sesion|' + secreto()).update(c).digest('base64url');
function iguales(a, b) { const x = Buffer.from(String(a)), y = Buffer.from(String(b)); return x.length === y.length && crypto.timingSafeEqual(x, y); }
function firmarSesion(u) {
  const ahora = Date.now(), c = Buffer.from(JSON.stringify({ u: u.usuario, n: u.nombre, f: u.filial, m: u.modulos || 'TODOS', e: u.eecc || undefined, cc: !!u.cambiar_clave, iat: ahora, exp: ahora + DURACION_SESION })).toString('base64url');
  return `u1.${c}.${firma(c)}`;
}
function leerSesion(t) {
  const m = /^u1\.([\w-]+)\.([\w-]+)$/.exec(String(t || '')); if (!m || !secreto() || !iguales(firma(m[1]), m[2])) return null;
  try { const p = JSON.parse(Buffer.from(m[1], 'base64url').toString()); return p.exp > Date.now() ? p : null; } catch { return null; }
}
let ACC = { en: 0, d: null };
async function accesoConfig(forzar) {
  if (!forzar && ACC.d && Date.now() - ACC.en < 60e3) return ACC.d;
  try { const { datos } = await consultar('ajustes?select=datos&clave=eq.acceso:GLOBAL'); ACC = { en: Date.now(), d: { general: true, rev: {}, ...(datos[0] ? datos[0].datos : {}) } }; }
  catch { if (!ACC.d) ACC = { en: Date.now(), d: { general: true, rev: {} } }; }
  return ACC.d;
}
async function guardarAcceso(d) { await guardar('ajustes', [{ clave: 'acceso:GLOBAL', datos: d, actualizado: new Date().toISOString() }], 'clave'); ACC = { en: Date.now(), d }; }
// Sesión del pedido (o null). permitirCambio: acepta la sesión de quien aún debe cambiar su clave (solo para cambiarla).
async function sesionDe(req, permitirCambio = false) {
  const t = String(req.headers['x-portal-token'] || ''), p = leerSesion(t);
  if (p) { if (p.cc && !permitirCambio) return null; const a = await accesoConfig(), r = a.rev && a.rev[p.u]; if (r && p.iat <= r) return null; return p; }
  const esperado = process.env.PORTAL_TOKEN;
  if (!esperado) return { u: 'general', n: 'General', f: 'TODAS' };
  if (!t || !iguales(t, esperado)) return null;
  const a = await accesoConfig(); return a.general === false ? null : { u: 'general', n: 'Clave general', f: 'TODAS' };
}
// Usuario de EECC (contratista): solo puede usar /api/eecc (sus pendientes y evidencias) y /api/usuarios (ingresar / cambiar clave).
const SOLO_EECC = /^\/api\/(eecc|usuarios)(\b|\/|\?|$)|^\/api\/zonificacion\?(?:[^#]*&)?areas=1(?:&|$)/;   // las EECC: su API, usuarios y solo las áreas de los planos (Ruteo)
async function autorizado(req) { const p = await sesionDe(req); if (p && p.e && !SOLO_EECC.test(String(req.url || ''))) return false; if (p) req.usuario = p; return !!p; }

function esMaster(req) {
  const esperado = process.env.MASTER_TOKEN || '', dado = String(req.headers['x-master-token'] || '');
  return !!esperado && !!dado && iguales(esperado, dado);
}

function responder(res, estado, cuerpo, cacheSeg = 0) {
  res.statusCode = estado;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Vary', 'x-portal-token');   // la caché del CDN se separa por sesión: cada usuario recibe solo lo suyo
  res.setHeader('Cache-Control', cacheSeg ? `s-maxage=${cacheSeg}, stale-while-revalidate=${cacheSeg * 4}` : 'no-store');
  res.end(JSON.stringify(cuerpo));
}

// Filial elegida en el portal (?filial=LIMA). Por defecto LIMA.
const FILIALES = ['LIMA', 'PROVINCIAS', 'ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
// Filiales que puede ver una sesión: TODAS, PROVINCIAS (las 7) o una sola.
// f puede ser una lista: "LIMA,PIURA". PROVINCIAS = las 7 provincias (y su vista conjunta); con las 7 marcadas también se ve la conjunta.
const PROV7 = ['ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
function permitidas(f) {
  const L = String(f || 'TODAS').toUpperCase().split(',').map(x => x.trim()).filter(Boolean);
  if (!L.length || L.includes('TODAS')) return FILIALES;
  const S = new Set(); L.forEach(x => { if (x === 'PROVINCIAS') { S.add('PROVINCIAS'); PROV7.forEach(p => S.add(p)); } else if (FILIALES.includes(x)) S.add(x); });
  if (PROV7.every(p => S.has(p))) S.add('PROVINCIAS');
  return FILIALES.filter(x => S.has(x));
}
// Ventanas (módulos) que puede ver la sesión: "TODOS" o lista "instalaciones,fftt"
const MODULOS_PORTAL = ['instalaciones', 'averias', 'observadas', 'anuladas', 'dashboard', 'fftt', 'speedtest', 'zonificacion'];
const puedeModulo = (req, ...ms) => { const m = req.usuario && req.usuario.m; if (!m || m === 'TODOS') return true; const L = m.split(','); return ms.some(x => L.includes(x)); };
const puedeVer = (req, filial) => { const u = req.usuario; return !u || !u.f || u.f === 'TODAS' || permitidas(u.f).includes(String(filial || '').toUpperCase()); };
function filialDe(req) {
  let f = (new URL(req.url, 'http://x').searchParams.get('filial') || 'LIMA').toUpperCase();
  if (!FILIALES.includes(f)) f = 'LIMA';
  const P = permitidas(req.usuario ? req.usuario.f : 'TODAS');   // un usuario de filial solo recibe su filial
  return P.includes(f) ? f : (P[0] || 'LIMA');
}

// Varias filiales a la vez (?filial=LIMA,AREQUIPA): cada una debe estar permitida; si ninguna lo está, la primera permitida.
function filialesDe(req) {
  const P = permitidas(req.usuario ? req.usuario.f : 'TODAS');
  const L = [...new Set((new URL(req.url, 'http://x').searchParams.get('filial') || 'LIMA').toUpperCase().split(',').map(x => x.trim()).filter(x => FILIALES.includes(x) && P.includes(x)))];
  return L.length ? L : [P[0] || 'LIMA'];
}
// Filtro de PostgREST por filial: PROVINCIAS = todas menos Lima; una lista = las filiales de la lista.
const filtroFilial = (f) => {
  const L = [...new Set((Array.isArray(f) ? f : [f]).flatMap(x => x === 'PROVINCIAS' ? PROV7 : [x]))];
  if (L.length === PROV7.length && PROV7.every(x => L.includes(x))) return 'filial=neq.LIMA';
  if (L.length === 1) return `filial=eq.${encodeURIComponent(L[0])}`;
  return 'filial=in.(' + L.map(x => encodeURIComponent(`"${x}"`)).join(',') + ')';
};

// Pedido genérico (PATCH / DELETE / POST) para la tabla de usuarios.
async function pedir(ruta, method, cuerpo, prefer = 'return=minimal') {
  if (!URL_BASE || !CLAVE) throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en Vercel');
  const r = await fetch(`${URL_BASE}/rest/v1/${ruta}`, { method, headers: { apikey: CLAVE, Authorization: `Bearer ${CLAVE}`, 'Content-Type': 'application/json', Prefer: prefer }, body: cuerpo == null ? undefined : JSON.stringify(cuerpo) });
  if (!r.ok) { const e = new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 300)}`); e.estado = r.status; throw e; }
  return prefer.includes('representation') ? r.json() : null;
}

module.exports = { PROV7, MODULOS_PORTAL, puedeModulo, consultar, leerTableros, guardar, pedir, autorizado, sesionDe, firmarSesion, accesoConfig, guardarAcceso, esMaster, iguales, responder, filialDe, filialesDe, filtroFilial, permitidas, puedeVer, FILIALES };
