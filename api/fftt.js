// GET /api/fftt → tablero del buscador FFTT (todas las filiales; la página filtra y valida el formato por planta).
const { consultar, autorizado, responder, puedeVer, puedeModulo } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'fftt')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  try {
    const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.fftt');
    if (!datos.length) return responder(res, 404, { error: 'Aún no hay datos de FFTT: aparecen después de la próxima actualización del robot.' });
    const d = datos[0].datos, fi = (d.cols || []).indexOf('fi');
    // Usuario de una filial: solo recibe las altas de su filial
    const filas = fi >= 0 && req.usuario && req.usuario.f !== 'TODAS' ? d.filas.filter(f => puedeVer(req, f[fi])) : d.filas;
    responder(res, 200, { ...d, filas, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
