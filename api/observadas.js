// GET /api/observadas?filial=LIMA
// Devuelve las OS en estado OBSERVADA de una filial (fecha de observación, motivo, submotivo…), que el robot arma en
// cada sincronización (tabla "tableros", clave "observadas:<FILIAL>").
const { consultar, autorizado, responder, filialDe } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const filial = filialDe(req);
  try {
    const { datos } = await consultar(`tableros?select=datos,actualizado&clave=eq.${encodeURIComponent('observadas:' + filial)}`);
    if (!datos.length) return responder(res, 404, { error: `Aún no hay datos de observadas para la filial ${filial}. Se generan en la próxima actualización del robot.` });
    responder(res, 200, { ...datos[0].datos, filial, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
