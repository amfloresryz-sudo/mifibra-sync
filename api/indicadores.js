// GET /api/indicadores?filial=LIMA
// Devuelve todo lo que necesita el Dashboard y las tarjetas de cada módulo en una sola llamada.
// El robot los calcula en cada sincronización y los guarda listos en la tabla "tableros" (clave "indicadores:<FILIAL>"):
// aquí solo se lee esa fila. Antes se calculaban en Supabase en cada visita y eso agotaba el disco del plan gratuito.
const { consultar, autorizado, responder, filialDe } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const filial = filialDe(req);
  try {
    const { datos } = await consultar(`tableros?select=datos,actualizado&clave=eq.${encodeURIComponent('indicadores:' + filial)}`);
    if (!datos.length) return responder(res, 404, { error: `Aún no hay indicadores para la filial ${filial}: espera la próxima actualización del robot.` });
    const d = datos[0].datos || {};
    responder(res, 200, {
      filial,
      indicadores: d.indicadores || null,
      por_dia: d.por_dia || [],
      por_estado: d.por_estado || {},
      ranking_tecnicos: d.ranking_tecnicos || [],
      actualizado: datos[0].actualizado,
    }, 120); // caché de 2 minutos en el borde de Vercel (el robot actualiza cada 20 min)
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
