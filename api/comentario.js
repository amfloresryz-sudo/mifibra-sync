// GET /api/comentario?id=<id_os>
// Comentario del técnico (OBS VISITA TÉCNICO) de una OS, para verlo al hacer clic en el AB.
// Se busca por id_os (llave primaria): una sola fila, sin recorrer la tabla. Los teléfonos ya vienen ocultos.
const { consultar, autorizado, responder, puedeVer, puedeModulo } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'instalaciones', 'observadas', 'averias')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  const id = String(parseInt(new URL(req.url, 'http://x').searchParams.get('id'), 10));
  if (!/^\d{1,12}$/.test(id)) return responder(res, 400, { error: 'OS no válida' });
  try {
    const { datos } = await consultar(`ordenes_servicio?select=nro_os,abonado_codigo,filial,estado_os,tecnico_ejecutor,obs_visita_todo,obs_visita,obs_visita_en&id_os=eq.${id}`);
    if (!datos.length || !puedeVer(req, datos[0].filial)) return responder(res, 404, { error: 'No se encontró la OS.' });
    responder(res, 200, datos[0], 120);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
