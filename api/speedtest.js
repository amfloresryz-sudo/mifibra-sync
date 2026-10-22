// GET /api/speedtest
// Tablero de Speed Test (todas las filiales): lo calcula el robot en cada sincronización con los enlaces de
// «Observaciones Tecnico» y lo guarda en la tabla "tableros" (clave "speedtest"). La página filtra en el navegador.
const { consultar, autorizado, responder } = require('./_supabase');

module.exports = async (req, res) => {
  if (!autorizado(req)) return responder(res, 401, { error: 'No autorizado' });
  try {
    // ?os=1,2,3: datos de esas OS de instalación para las listas de AB (F. Recep, F. atención, distrito y estado)
    const os = new URL(req.url, 'http://x').searchParams.get('os');
    if (os != null) {
      const L = [...new Set(os.split(',').map(v => parseInt(v, 10)).filter(v => v > 0))].slice(0, 300);
      if (!L.length) return responder(res, 200, []);
      const { datos } = await consultar(`ordenes_servicio?select=nro_os,abonado_codigo,filial,fecha_registro,fecha_atencion,distrito,estado_os&tipo_orden=eq.${encodeURIComponent('INSTALACION SERVICIO')}&nro_os=in.(${L.join(',')})`);
      return responder(res, 200, datos);
    }
    const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.speedtest');
    if (!datos.length) return responder(res, 404, { error: 'Aún no hay datos de Speed Test: aparecen después de la próxima actualización del robot.' });
    responder(res, 200, { ...datos[0].datos, actualizado: datos[0].actualizado }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
