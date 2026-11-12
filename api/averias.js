// GET /api/averias?filial=LIMA[&mes=AAAA-MM]
// Devuelve el tablero de Averías y TC de una filial, que el robot calcula en cada sincronización (tabla "tableros").
const { consultar, leerTableros, autorizado, responder, filialDe, puedeModulo } = require('./_supabase');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const sp = new URL(req.url, 'http://x').searchParams;
  // ?para=sup: Supervisión de Instalaciones (quien tiene Instalaciones aunque no tenga Averías) recibe SOLO las averías
  // de HOY (liquidadas y en curso) para contarlas y dibujarlas en la línea del tiempo de cada técnico.
  const paraSup = sp.get('para') === 'sup' && !sp.get('mes') && puedeModulo(req, 'instalaciones');
  if (!paraSup && !puedeModulo(req, 'averias')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  const filial = filialDe(req);
  try {
    // ?mes=AAAA-MM: tablero de un mes cerrado (el robot guarda el mes anterior); sin mes = mes en curso.
    const mes = sp.get('mes') || '';
    const clave = 'averias:' + filial + (/^\d{4}-\d{2}$/.test(mes) ? ':' + mes : '');
    // ?solo=hora: solo la hora de los datos (consulta liviana que el portal hace cada minuto para saber si hay algo nuevo)
    if (sp.get('solo') === 'hora') {
      const { datos } = await consultar(`tableros?select=actualizado&clave=eq.${encodeURIComponent(clave)}`);
      return responder(res, 200, { actualizado: datos[0]?.actualizado || null }, 10);
    }
    const { datos } = await leerTableros(clave, 'datos,actualizado');   // con memoria: solo baja de Supabase si el robot actualizó
    if (!datos.length) return responder(res, 404, { error: `Aún no hay datos de averías para la filial ${filial}.` });
    if (paraSup) {
      const T = datos[0].datos || {}, hoy = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Lima' });
      return responder(res, 200, { mes: T.mes, anio: T.anio, dias: T.dias, filial, actualizado: datos[0].actualizado,
        liq: (T.liq || []).filter(x => x.d === T.dias), pend: (T.pend || []).filter(p => p.ini && String(p.ini).slice(0, 10) === hoy) }, 60);
    }
    responder(res, 200, { ...datos[0].datos, filial, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
