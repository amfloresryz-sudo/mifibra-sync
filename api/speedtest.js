// GET /api/speedtest
// Tablero de Speed Test (todas las filiales): lo calcula el robot en cada sincronización con los enlaces de
// «Observaciones Tecnico» y lo guarda en la tabla "tableros" (clave "speedtest"). La página filtra en el navegador.
const { consultar, autorizado, responder } = require('./_supabase');

module.exports = async (req, res) => {
  if (!autorizado(req)) return responder(res, 401, { error: 'No autorizado' });
  try {
    const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.speedtest');
    if (!datos.length) return responder(res, 404, { error: 'Aún no hay datos de Speed Test: aparecen después de la próxima actualización del robot.' });
    responder(res, 200, { ...datos[0].datos, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
