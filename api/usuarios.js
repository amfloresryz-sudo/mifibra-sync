// /api/usuarios — usuarios propios del portal.
// POST ?accion=ingresar {usuario, clave}        → sesión (token firmado 12 h). Usuario vacío = clave general (mientras esté activa).
// POST ?accion=cambiar  {nueva}                  → el usuario cambia su clave (obligatorio en el primer ingreso).
// Solo con la clave master (header x-master-token):
// GET  ?accion=lista                             → usuarios (sin claves) y si la clave general sigue activa.
// POST ?accion=guardar  {usuario, nombre, filial, activo, nuevo, reset} → crea / edita / restablece la clave.
// POST ?accion=masivo   {filas:[{nombre, usuario, filial}]}             → crea varios de una vez (clave inicial para cada uno).
// POST ?accion=renombrar {usuario, nuevo_usuario, nombre} → edita el usuario y el nombre
// POST ?accion=borrar   {usuario}  ·  POST ?accion=general {activa}
const crypto = require('crypto');
const { consultar, pedir, sesionDe, firmarSesion, accesoConfig, guardarAcceso, esMaster, iguales, responder, FILIALES } = require('./_supabase');

const FIL_OK = ['TODAS', ...FILIALES];
const limpioU = (v) => String(v || '').trim().toLowerCase();
const nombreOk = (v) => String(v || '').replace(/[\u0000-\u001f<>]/g, '').replace(/\s+/g, ' ').trim().slice(0, 60);
const usuarioValido = (u) => /^[a-z0-9._-]{3,30}$/.test(u);
const ALFA = 'abcdefghjkmnpqrstuvwxyz23456789';
const claveInicial = () => { const b = crypto.randomBytes(8); let s = ''; for (const x of b) s += ALFA[x % ALFA.length]; return s; };
function hash(clave) { const sal = crypto.randomBytes(16).toString('hex'); return `s1$${sal}$${crypto.scryptSync(String(clave), sal, 32).toString('hex')}`; }
function verificar(clave, h) { const [v, sal, hx] = String(h || '').split('$'); if (v !== 's1' || !sal || !hx) return false; return iguales(crypto.scryptSync(String(clave), sal, 32).toString('hex'), hx); }
const publico = (u) => ({ usuario: u.usuario, nombre: u.nombre, filial: u.filial, activo: u.activo, cambiar_clave: u.cambiar_clave, creado_en: u.creado_en, clave_en: u.clave_en, ultimo_ingreso: u.ultimo_ingreso });

async function leerCuerpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  let s = ''; for await (const c of req) { s += c; if (s.length > 200000) throw new Error('Cuerpo demasiado grande'); }
  return JSON.parse(s || '{}');
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
        if (esperado && clave && iguales(clave, esperado) && a.general !== false) return responder(res, 200, { token: esperado, nombre: 'Clave general', filial: 'TODAS', cambiar: false });
        return responder(res, 401, { error: a.general === false ? 'La clave general ya no está activa: ingresa con tu usuario.' : 'Contraseña incorrecta.' });
      }
      const u = await buscar(usuario);
      if (!u || !verificar(clave, u.clave_hash)) return responder(res, 401, { error: 'Usuario o contraseña incorrectos.' });
      if (!u.activo) return responder(res, 403, { error: 'Tu usuario está desactivado. Pide al administrador que lo active.' });
      await pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', { ultimo_ingreso: new Date().toISOString() });
      return responder(res, 200, { token: firmarSesion(u), nombre: u.nombre, filial: u.filial, cambiar: u.cambiar_clave, usuario: u.usuario });
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
      return responder(res, 200, { token: firmarSesion({ ...u, ...cambios }), nombre: u.nombre, filial: u.filial, usuario: u.usuario });
    }

    // ---------- Administración (clave master) ----------
    if (!process.env.MASTER_TOKEN) return responder(res, 403, { error: 'Falta configurar la clave master (MASTER_TOKEN) en Vercel.' });
    if (!esMaster(req)) return responder(res, 403, { error: 'Clave master incorrecta.' });
    if (req.method === 'GET' && accion === 'lista') {
      const { datos } = await consultar('usuarios?select=*&order=filial.asc,nombre.asc');
      const a = await accesoConfig(true);
      return responder(res, 200, { usuarios: datos.map(publico), general: a.general !== false, hayGeneral: !!process.env.PORTAL_TOKEN });
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
        const usuario = limpioU(f.usuario), nombre = nombreOk(f.nombre), filial = String(f.filial || '').trim().toUpperCase();
        if (!usuarioValido(usuario)) { omitidos.push({ usuario: f.usuario, motivo: 'usuario no válido (3 a 30 letras, números, punto o guion)' }); continue; }
        if (!FIL_OK.includes(filial)) { omitidos.push({ usuario, motivo: `filial «${f.filial}» no existe` }); continue; }
        if (existe.has(usuario)) { omitidos.push({ usuario, motivo: 'ya existe' }); continue; }
        existe.add(usuario); const clave = claveInicial();
        nuevos.push({ usuario, nombre: nombre || usuario, filial, clave_hash: hash(clave), cambiar_clave: true, activo: true }); creados.push({ usuario, nombre: nombre || usuario, filial, clave });
      }
      if (nuevos.length) await pedir('usuarios', 'POST', nuevos);
      return responder(res, 200, { creados, omitidos });
    }
    if (accion === 'guardar') {
      const usuario = limpioU(b.usuario), filial = String(b.filial || '').toUpperCase(), nombre = nombreOk(b.nombre);
      if (!usuarioValido(usuario)) return responder(res, 400, { error: 'Usuario no válido: de 3 a 30 letras, números, punto o guion (sin espacios ni tildes).' });
      if (!FIL_OK.includes(filial)) return responder(res, 400, { error: 'Filial no válida.' });
      const previo = await buscar(usuario);
      if (b.nuevo) {
        if (previo) return responder(res, 400, { error: 'Ese usuario ya existe.' });
        const clave = claveInicial();
        await pedir('usuarios', 'POST', [{ usuario, nombre: nombre || usuario, filial, clave_hash: hash(clave), cambiar_clave: true, activo: true }]);
        return responder(res, 200, { creado: { usuario, nombre: nombre || usuario, filial, clave } });
      }
      if (!previo) return responder(res, 404, { error: 'No existe ese usuario.' });
      const cambios = { nombre: nombre || previo.nombre, filial, activo: b.activo !== false };
      let clave;
      if (b.reset) { clave = claveInicial(); Object.assign(cambios, { clave_hash: hash(clave), cambiar_clave: true, clave_en: new Date().toISOString() }); }
      await pedir(`usuarios?usuario=eq.${encodeURIComponent(usuario)}`, 'PATCH', cambios);
      if (b.reset || filial !== previo.filial || cambios.activo !== previo.activo || cambios.nombre !== previo.nombre) await revocar([usuario]);
      return responder(res, 200, clave ? { clave } : { ok: true });
    }
    responder(res, 400, { error: 'Acción no válida' });
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
