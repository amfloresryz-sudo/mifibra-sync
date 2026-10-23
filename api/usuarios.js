// /api/usuarios — usuarios propios del portal.
// POST ?accion=ingresar {usuario, clave}        → sesión (token firmado 12 h). Usuario vacío = clave general (mientras esté activa).
// POST ?accion=cambiar  {nueva}                  → el usuario cambia su clave (obligatorio en el primer ingreso).
// Solo con la clave master (header x-master-token):
// GET  ?accion=lista                             → usuarios (sin claves) y si la clave general sigue activa.
// POST ?accion=guardar  {usuario, nombre, filial, activo, nuevo, reset} → crea / edita / restablece la clave.
// POST ?accion=masivo   {filas:[{nombre, usuario, filial}]}             → crea varios de una vez (clave inicial para cada uno).
// POST ?accion=renombrar {usuario, nuevo_usuario, nombre} → edita el usuario y el nombre
// GET  ?accion=ingresos&usuario=x → historial de ingresos (ciudad aproximada, equipo)
// POST ?accion=borrar   {usuario}  ·  POST ?accion=general {activa}
const crypto = require('crypto');
const { PROV7, MODULOS_PORTAL, consultar, pedir, sesionDe, firmarSesion, accesoConfig, guardarAcceso, esMaster, iguales, responder, FILIALES } = require('./_supabase');

const FIL_OK = ['TODAS', ...FILIALES];
// Filiales: una o varias ("LIMA,PIURA"); TODAS gana; las 7 provincias = PROVINCIAS. null si alguna no existe.
function filialesOk(v) {
  const L = [...new Set(String(v || '').toUpperCase().split(/[,;\/|]+/).map(x => x.trim()).filter(Boolean))];
  if (!L.length || L.some(x => !FIL_OK.includes(x))) return null;
  if (L.includes('TODAS') || (L.includes('LIMA') && (L.includes('PROVINCIAS') || PROV7.every(p => L.includes(p))))) return 'TODAS';
  let R = L.includes('PROVINCIAS') || PROV7.every(p => L.includes(p)) ? ['PROVINCIAS', ...L.filter(x => x === 'LIMA')] : L;
  return FIL_OK.filter(x => R.includes(x)).join(',');
}
// Ventanas: TODOS o lista de módulos
function modulosOk(v) {
  const L = [...new Set(String(v || 'TODOS').toLowerCase().split(/[,;\/|]+/).map(x => x.trim()).filter(Boolean))];
  if (!L.length || L.includes('todos') || MODULOS_PORTAL.every(m => L.includes(m))) return 'TODOS';
  const R = MODULOS_PORTAL.filter(m => L.includes(m)); return R.length ? R.join(',') : null;
}
// Usuario sin distinguir mayúsculas, espacios ni tildes (RDiaz, rdiaz y « rdíaz » son el mismo)
const limpioU = (v) => String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, '').toLowerCase();
const nombreOk = (v) => String(v || '').replace(/[\u0000-\u001f<>]/g, '').replace(/\s+/g, ' ').trim().slice(0, 60);
const usuarioValido = (u) => /^[a-z0-9._-]{3,30}$/.test(u);
const ALFA = 'abcdefghjkmnpqrstuvwxyz23456789';
const claveInicial = () => { const b = crypto.randomBytes(8); let s = ''; for (const x of b) s += ALFA[x % ALFA.length]; return s; };
function hash(clave) { const sal = crypto.randomBytes(16).toString('hex'); return `s1$${sal}$${crypto.scryptSync(String(clave), sal, 32).toString('hex')}`; }
function verificar(clave, h) { const [v, sal, hx] = String(h || '').split('$'); if (v !== 's1' || !sal || !hx) return false; return iguales(crypto.scryptSync(String(clave), sal, 32).toString('hex'), hx); }
const publico = (u) => ({ usuario: u.usuario, nombre: u.nombre, filial: u.filial, modulos: u.modulos || 'TODOS', activo: u.activo, cambiar_clave: u.cambiar_clave, creado_en: u.creado_en, clave_en: u.clave_en, ultimo_ingreso: u.ultimo_ingreso });

async function leerCuerpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  let s = ''; for await (const c of req) { s += c; if (s.length > 200000) throw new Error('Cuerpo demasiado grande'); }
  return JSON.parse(s || '{}');
}
// ---------- Ubicación aproximada del ingreso (la entrega Vercel según la conexión; sin pedir permiso) ----------
const REG = { LMA: 'Lima Metropolitana', LIM: 'Lima', CAL: 'Callao', ANC: 'Áncash', ARE: 'Arequipa', CAJ: 'Cajamarca', HUC: 'Huánuco', LAL: 'La Libertad', LAM: 'Lambayeque', PIU: 'Piura',
  AMA: 'Amazonas', APU: 'Apurímac', AYA: 'Ayacucho', CUS: 'Cusco', HUV: 'Huancavelica', ICA: 'Ica', JUN: 'Junín', LOR: 'Loreto', MDD: 'Madre de Dios', MOQ: 'Moquegua', PAS: 'Pasco', PUN: 'Puno', SAM: 'San Martín', TAC: 'Tacna', TUM: 'Tumbes', UCA: 'Ucayali' };
const REG_FIL = { LIMA: ['LMA', 'LIM', 'CAL'], ANCASH: ['ANC'], AREQUIPA: ['ARE'], CAJAMARCA: ['CAJ'], HUANUCO: ['HUC'], 'LA LIBERTAD': ['LAL'], LAMBAYEQUE: ['LAM'], PIURA: ['PIU'] };
REG_FIL.PROVINCIAS = ['ANC', 'ARE', 'CAJ', 'HUC', 'LAL', 'LAM', 'PIU'];
function equipo(ua) { ua = String(ua || ''); const cel = /Mobi|Android|iPhone|iPad/i.test(ua) ? 'Celular' : 'PC';
  const nav = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Navegador'; return `${cel} · ${nav}`; }
async function registrarIngreso(req, usuario, filial) {
  try {
    const h = (k) => { const v = req.headers[k]; try { return v ? decodeURIComponent(String(v)) : ''; } catch { return String(v || ''); } };
    const pais = h('x-vercel-ip-country'), cod = h('x-vercel-ip-country-region'), lat = parseFloat(h('x-vercel-ip-latitude')), lon = parseFloat(h('x-vercel-ip-longitude'));
    const region = pais === 'PE' ? (REG[cod] || cod) : cod, toks = String(filial || '').split(','), okReg = toks.includes('TODAS') ? null : toks.flatMap(t => REG_FIL[t] || []);
    const fuera = !!pais && (pais !== 'PE' || (!!okReg && okReg.length > 0 && !!cod && !okReg.includes(cod)));   // ingresó desde fuera de su filial
    await pedir('ingresos', 'POST', [{ usuario, filial, ciudad: h('x-vercel-ip-city') || null, region: region || null, pais: pais || null, lat: isFinite(lat) ? lat : null, lon: isFinite(lon) ? lon : null, equipo: equipo(req.headers['user-agent']), fuera }]);
    if (Math.random() < 0.02) await pedir(`ingresos?en=lt.${new Date(Date.now() - 180 * 864e5).toISOString()}`, 'DELETE');   // se guardan 6 meses
  } catch { /* el registro nunca impide ingresar */ }
}
async function buscar(usuario) { const { datos } = await consultar(`usuarios?select=*&usuario=eq.${encodeURIComponent(usuario)}`); return datos[0] || null; }
// Invalida al instante las sesiones abiertas de esos usuarios (cambió su filial, se desactivó o se restableció su clave).
async function revocar(usuarios) { const a = await accesoConfig(true), rev = { ...(a.rev || {}) }, t = Date.now(); usuarios.forEach(u => rev[u] = t); await guardarAcceso({ ...a, rev }); }

module.exports = async (req, res) => {
  const accion = new URL(req.url, 'http://x').searchParams.get('accion') || '';
  try {
    if (req.method === 'POST' && accion === 'ingresar') {
      const b = await leerCuerpo(req), usuario = limpioU(b.usuario), clave = String(b.clave || '');
      if (!usuario) {   // clave general (compartida), mientras no se desactive
        const esperado = process.env.PORTAL_TOKEN, a = await accesoConfig(true);
        if (esperado && clave && iguales(clave, esperado) && a.general !== false) { await registrarIngreso(req, 'general', 'TODAS'); return responder(res, 200, { token: esperado, nombre: 'Clave general', filial: 'TODAS', modulos: 'TODOS', cambiar: false }); }
        return responder(res, 401, { error: a.general === false ? 'La clave general ya no está activa: ingresa con tu usuario.' : 'Contraseña incorrecta.' });
      }
      const u = await buscar(usuario);
      // La contraseña inicial (generada en minúsculas) se acepta aunque el celular ponga mayúsculas; la propia sí distingue
      const okClave = !!u && (verificar(clave, u.clave_hash) || (u.cambiar_clave && verificar(clave.trim().toLowerCase(), u.clave_hash)));
      if (!u || !okClave) return responder(res, 401, { error: 'Usuario o contraseña incorrectos.' });
      if (!u.activo) return responder(res, 403, { error: 'Tu usuario está desactivado. Pide al administrador que lo active.' });
      await Promise.all([pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', { ultimo_ingreso: new Date().toISOString() }), registrarIngreso(req, u.usuario, u.filial)]);
      return responder(res, 200, { token: firmarSesion(u), nombre: u.nombre, filial: u.filial, modulos: u.modulos || 'TODOS', cambiar: u.cambiar_clave, usuario: u.usuario });
    }
    if (req.method === 'POST' && accion === 'cambiar') {
      const p = await sesionDe(req, true);
      if (!p || p.u === 'general') return responder(res, 401, { error: 'Tu sesión venció: vuelve a ingresar.' });
      const nueva = String((await leerCuerpo(req)).nueva || '');
      if (nueva.length < 6) return responder(res, 400, { error: 'La contraseña debe tener al menos 6 caracteres.' });
      if (nueva.toLowerCase() === p.u) return responder(res, 400, { error: 'La contraseña no puede ser igual a tu usuario.' });
      const u = await buscar(p.u);
      if (!u || !u.activo) return responder(res, 403, { error: 'Tu usuario no está activo.' });
      if (verificar(nueva, u.clave_hash)) return responder(res, 400, { error: 'Elige una contraseña distinta a la inicial.' });
      const cambios = { clave_hash: hash(nueva), cambiar_clave: false, clave_en: new Date().toISOString() };
      await pedir(`usuarios?usuario=eq.${encodeURIComponent(p.u)}`, 'PATCH', cambios);
      return responder(res, 200, { token: firmarSesion({ ...u, ...cambios }), nombre: u.nombre, filial: u.filial, modulos: u.modulos || 'TODOS', usuario: u.usuario });
    }

    // ---------- Administración (clave master) ----------
    if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
    if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta.' });
    if (req.method === 'GET' && accion === 'lista') {
      const { datos } = await consultar('usuarios?select=*&order=filial.asc,nombre.asc');
      const a = await accesoConfig(true);
      // Último ingreso de cada uno (ubicación) y los ingresos fuera de su filial de los últimos 7 días
      const { datos: ing } = await consultar(`ingresos?select=usuario,en,ciudad,region,pais,lat,lon,equipo,fuera&en=gte.${new Date(Date.now() - 60 * 864e5).toISOString()}&order=en.desc&limit=5000`);
      const ult = {}, fuera7 = {}, hace7 = Date.now() - 7 * 864e5;
      ing.forEach(x => { if (!ult[x.usuario]) ult[x.usuario] = x; if (x.fuera && new Date(x.en) >= hace7) fuera7[x.usuario] = (fuera7[x.usuario] || 0) + 1; });
      return responder(res, 200, { usuarios: datos.map(u => ({ ...publico(u), lugar: ult[u.usuario] || null, fuera7: fuera7[u.usuario] || 0 })), general: a.general !== false, hayGeneral: !!process.env.PORTAL_TOKEN,
        generalLugar: ult.general || null });
    }
    if (req.method === 'GET' && accion === 'ingresos') {   // historial de un usuario (últimos 100)
      const usuario = limpioU(new URL(req.url, 'http://x').searchParams.get('usuario'));
      const { datos } = await consultar(`ingresos?select=en,ciudad,region,pais,lat,lon,equipo,fuera&usuario=eq.${encodeURIComponent(usuario)}&order=en.desc&limit=100`);
      return responder(res, 200, datos);
    }
    if (req.method !== 'POST') return responder(res, 405, { error: 'Método no permitido' });
    const b = await leerCuerpo(req);
    if (accion === 'general') { const a = await accesoConfig(true); await guardarAcceso({ ...a, general: !!b.activa }); return responder(res, 200, { general: !!b.activa }); }
    if (accion === 'renombrar') {   // cambia el usuario (y el nombre): la contraseña se mantiene, la sesión abierta se cierra
      const usuario = limpioU(b.usuario), nuevo = limpioU(b.nuevo_usuario), nombre = nombreOk(b.nombre);
      if (!usuarioValido(nuevo)) return responder(res, 400, { error: 'Usuario no válido: de 3 a 30 letras, números, punto o guion (sin espacios ni tildes).' });
      const previo = await buscar(usuario); if (!previo) return responder(res, 404, { error: 'No existe ese usuario.' });
      if (nuevo !== usuario && await buscar(nuevo)) return responder(res, 400, { error: `El usuario «${nuevo}» ya existe.` });
      await pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', { usuario: nuevo, nombre: nombre || previo.nombre });
      if (nuevo !== usuario) await pedir(`ingresos?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', { usuario: nuevo }).catch(() => {});
      if (nuevo !== usuario) await revocar([usuario, nuevo]); else if (nombre !== previo.nombre) await revocar([usuario]);
      return responder(res, 200, { ok: true });
    }
    if (accion === 'borrar') {
      const usuario = limpioU(b.usuario); await pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'DELETE'); await revocar([usuario]);
      return responder(res, 200, { ok: true });
    }
    if (accion === 'masivo') {
      const filas = Array.isArray(b.filas) ? b.filas.slice(0, 300) : [], { datos } = await consultar('usuarios?select=usuario');
      const existe = new Set(datos.map(x => x.usuario)), creados = [], omitidos = [], nuevos = [];
      for (const f of filas) {
        const usuario = limpioU(f.usuario), nombre = nombreOk(f.nombre), filial = filialesOk(f.filial), modulos = modulosOk(f.modulos);
        if (!usuarioValido(usuario)) { omitidos.push({ usuario: f.usuario, motivo: 'usuario no válido (3 a 30 letras, números, punto o guion)' }); continue; }
        if (!filial) { omitidos.push({ usuario, motivo: `filial «${f.filial}» no existe` }); continue; }
        if (!modulos) { omitidos.push({ usuario, motivo: 'elige al menos una ventana' }); continue; }
        if (existe.has(usuario)) { omitidos.push({ usuario, motivo: 'ya existe' }); continue; }
        existe.add(usuario); const clave = claveInicial();
        nuevos.push({ usuario, nombre: nombre || usuario, filial, modulos, clave_hash: hash(clave), cambiar_clave: true, activo: true }); creados.push({ usuario, nombre: nombre || usuario, filial, modulos, clave });
      }
      if (nuevos.length) await pedir('usuarios', 'POST', nuevos);
      return responder(res, 200, { creados, omitidos });
    }
    if (accion === 'guardar') {
      const usuario = limpioU(b.usuario), filial = filialesOk(b.filial), nombre = nombreOk(b.nombre), modulos = modulosOk(b.modulos);
      if (!usuarioValido(usuario)) return responder(res, 400, { error: 'Usuario no válido: de 3 a 30 letras, números, punto o guion (sin espacios ni tildes).' });
      if (!filial) return responder(res, 400, { error: 'Elige al menos una filial válida.' });
      if (!modulos) return responder(res, 400, { error: 'Elige al menos una ventana.' });
      const previo = await buscar(usuario);
      if (b.nuevo) {
        if (previo) return responder(res, 400, { error: 'Ese usuario ya existe.' });
        const clave = claveInicial();
        await pedir('usuarios', 'POST', [{ usuario, nombre: nombre || usuario, filial, modulos, clave_hash: hash(clave), cambiar_clave: true, activo: true }]);
        return responder(res, 200, { creado: { usuario, nombre: nombre || usuario, filial, modulos, clave } });
      }
      if (!previo) return responder(res, 404, { error: 'No existe ese usuario.' });
      const cambios = { nombre: nombre || previo.nombre, filial, modulos, activo: b.activo !== false };
      let clave;
      if (b.reset) { clave = claveInicial(); Object.assign(cambios, { clave_hash: hash(clave), cambiar_clave: true, clave_en: new Date().toISOString() }); }
      await pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', cambios);
      if (b.reset || filial !== previo.filial || modulos !== (previo.modulos || 'TODOS') || cambios.activo !== previo.activo || cambios.nombre !== previo.nombre) await revocar([usuario]);
      return responder(res, 200, clave ? { clave } : { ok: true });
    }
    responder(res, 400, { error: 'Acción no válida' });
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
