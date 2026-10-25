// GET /api/zonificacion
// Todos los planos de cada distrito (catálogo de planos de Lima + planos que aparecen en las órdenes). Lo arma el robot
// en cada sincronización (tabla "tableros", clave "zonificacion"); un usuario de filial solo recibe sus filiales.
const { consultar, autorizado, responder, puedeVer, puedeModulo } = require('./_supabase');

let MEM = { en: 0, d: null };
async function tablero() {
  if (MEM.d && Date.now() - MEM.en < 120e3) return MEM.d;
  const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.zonificacion');
  MEM = { en: Date.now(), d: datos[0] || null }; return MEM.d;
}

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'zonificacion', 'fftt')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  try {
    const t = await tablero();
    if (!t) return responder(res, 404, { error: 'Aún no hay datos de Zonificación: aparecen después de la próxima actualización del robot.' });
    const d = t.datos;
    const filas = req.usuario && req.usuario.f && req.usuario.f !== 'TODAS' ? d.filas.filter(f => puedeVer(req, f[0])) : d.filas;
    responder(res, 200, { ...d, filas, actualizado: t.actualizado }, 120);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
