// GET /api/fftt → tablero del buscador FFTT (todas las filiales; la página filtra y valida el formato por planta).
const { consultar, autorizado, responder } = require('./_supabase');

module.exports = async (req, res) => {
  if (!autorizado(req)) return responder(res, 401, { error: 'No autorizado' });
  try {
    const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.fftt');
    if (!datos.length) return responder(res, 404, { error: 'Aún no hay datos de FFTT: aparecen después de la próxima actualización del robot.' });
    responder(res, 200, { ...datos[0].datos, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
