// GET /api/averias?filial=LIMA[&mes=AAAA-MM]
// Devuelve el tablero de Averías y TC de una filial, que el robot calcula en cada sincronización (tabla "tableros").
const { consultar, autorizado, responder, filialDe, puedeModulo } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'averias')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  const filial = filialDe(req);
  try {
    // ?mes=AAAA-MM: tablero de un mes cerrado (el robot guarda el mes anterior); sin mes = mes en curso.
    const mes = new URL(req.url, 'http://x').searchParams.get('mes') || '';
    const clave = 'averias:' + filial + (/^\d{4}-\d{2}$/.test(mes) ? ':' + mes : '');
    const { datos } = await consultar(`tableros?select=datos,actualizado&clave=eq.${encodeURIComponent(clave)}`);
    if (!datos.length) return responder(res, 404, { error: `Aún no hay datos de averías para la filial ${filial}.` });
    responder(res, 200, { ...datos[0].datos, filial, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
