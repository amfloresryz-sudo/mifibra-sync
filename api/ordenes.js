// GET /api/ordenes?modulo=instalaciones|averias|observadas|anuladas
//      &estado=DESCARGADA &motivo=... &distrito=... &tecnico=... &desde=2026-10-01 &hasta=2026-10-14
//      &buscar=12345 &pagina=1 &por_pagina=50
// Devuelve las filas de la tabla de cada módulo, paginadas.
const { consultar, autorizado, responder, filialDe, filtroFilial, puedeModulo } = require('./_supabase');

const COLUMNAS = {
  instalaciones: 'id_os,nro_os,abonado_codigo,distrito,categoria,tarifa_servicio,fecha_registro,horario_atencion,tecnico_ejecutor,contrata,estado_os',
  averias: 'id_os,nro_os,abonado_codigo,distrito,motivo,lugar_averia,causa,sub_causa,solucion,tecnico_ejecutor,estado_os,fecha_registro,atencion_fin',
  observadas: 'id_os,nro_os,abonado_codigo,distrito,tipo_orden,detalle,motivo_observacion,submotivo_observacion,fecha_observacion,usuario_observacion,canal_atencion',
  anuladas: 'id_os,nro_os,tipo_orden,detalle,abonado_codigo,distrito,motivo_anulacion,fecha_mod,usuario_mod,canal_atencion',
};

const FILTRO_BASE = {
  instalaciones: `tipo_orden=eq.${encodeURIComponent('INSTALACION SERVICIO')}`,
  averias: `tipo_orden=eq.${encodeURIComponent('SERVICIO TECNICO')}`,
  observadas: 'estado_os=eq.OBSERVADA',
  anuladas: 'estado_os=eq.ANULADA',
};

// Filtros permitidos: parámetro -> columna (texto exacto).
const FILTROS = {
  estado: 'estado_os',
  tipo: 'tipo_orden',
  detalle: 'detalle',
  distrito: 'distrito',
  tecnico: 'tecnico_ejecutor',
  contrata: 'contrata',
  horario: 'horario_atencion',
  motivo: 'motivo',
  lugar: 'lugar_averia',
  causa: 'causa',
  motivo_observacion: 'motivo_observacion',
  submotivo: 'submotivo_observacion',
  motivo_anulacion: 'motivo_anulacion',
  canal: 'canal_atencion',
};

// Segmentador por grupo de canal: IC / VI exactos; OT = cualquier otro canal o vacío.
const CANAL_GRUPO = {
  IC: `canal_atencion=eq.${encodeURIComponent('INTEGRAL DE CAMPO')}`,
  VI: `canal_atencion=eq.${encodeURIComponent('VENTAS INBOUND')}`,
  OT: `and=${encodeURIComponent('(or(canal_atencion.is.null,canal_atencion.not.in.("INTEGRAL DE CAMPO","VENTAS INBOUND")))')}`,
};

// Ficha de una OS de avería (detalle del AB en el gráfico de 100 min): cómo se solucionó y los comentarios.
const COLUMNAS_FICHA = 'nro_os,abonado_codigo,estado_os,distrito,segmento,servicio,motivo,lugar_averia,detalle,fecha_registro,horario_atencion,'
  + 'atencion_inicio,atencion_fin,tecnico_ejecutor,tecnico_responsable,contrata,causa,sub_causa,solucion,tarifa_servicio,plano,tipo_vivienda,'
  + 'coordenadas,observaciones,obs_visita_todo,obs_visita_en';

const fechaValida = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '');

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const q = new URL(req.url, 'http://x').searchParams;
  if (q.has('ficha')) {
    const os = String(parseInt(q.get('ficha'), 10));
    if (!/^\d{1,10}$/.test(os)) return responder(res, 400, { error: 'OS no válida' });
    try {
      const { datos } = await consultar(`ordenes_servicio?select=${COLUMNAS_FICHA}&nro_os=eq.${os}&${filtroFilial(filialDe(req))}`
        + `&tipo_orden=eq.${encodeURIComponent('SERVICIO TECNICO')}&order=fecha_registro.desc&limit=1`);
      return responder(res, 200, { fila: datos[0] || null }, 60);
    } catch (e) { return responder(res, 500, { error: e.message }); }
  }
  const modulo = q.get('modulo');
  if (!COLUMNAS[modulo]) return responder(res, 400, { error: 'modulo debe ser instalaciones, averias, observadas o anuladas' });
  if (!puedeModulo(req, modulo, ...(modulo === 'instalaciones' || modulo === 'averias' ? ['observadas', 'averias', 'instalaciones'] : []))) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });

  const partes = [`select=${COLUMNAS[modulo]}`, FILTRO_BASE[modulo], filtroFilial(filialDe(req))];
  for (const [param, col] of Object.entries(FILTROS)) {
    const v = q.get(param);
    if (v) partes.push(`${col}=eq.${encodeURIComponent(v)}`);
  }
  if (CANAL_GRUPO[q.get('canal_grupo')]) partes.push(CANAL_GRUPO[q.get('canal_grupo')]);
  if (fechaValida(q.get('desde'))) partes.push(`fecha_registro=gte.${q.get('desde')}T00:00:00-05:00`);
  if (fechaValida(q.get('hasta'))) partes.push(`fecha_registro=lte.${q.get('hasta')}T23:59:59-05:00`);
  const buscar = (q.get('buscar') || '').trim();
  if (/^\d{1,12}$/.test(buscar)) partes.push(`or=(nro_os.eq.${buscar},abonado_codigo.eq.${buscar},id_os.eq.${buscar})`);

  const porPagina = Math.min(Math.max(parseInt(q.get('por_pagina') || '50', 10) || 50, 1), 200);
  const pagina = Math.max(parseInt(q.get('pagina') || '1', 10) || 1, 1);
  partes.push('order=fecha_registro.desc', `limit=${porPagina}`, `offset=${(pagina - 1) * porPagina}`);

  try {
    // Solo el periodo del portal (mes anterior + actual): se consulta la vista v_periodo.
    const { datos, total } = await consultar(`v_periodo?${partes.join('&')}`, { contar: true });
    responder(res, 200, { modulo, pagina, por_pagina: porPagina, total, filas: datos }, 60);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
