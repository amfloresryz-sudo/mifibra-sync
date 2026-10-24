/* Portal de Operaciones Mi Fibra: lee /api/indicadores y /api/ordenes y pinta cada módulo. */
(function () {
  'use strict';

  const CLAVE_SESION = 'mifibra_portal_token';
  const vista = document.getElementById('vista');
  const acceso = document.getElementById('acceso');
  let cacheIndicadores = null;
  const CLAVE_FILIAL = 'mifibra_filial';
  const filial = () => { try { return sessionStorage.getItem(CLAVE_FILIAL) || 'LIMA'; } catch { return 'LIMA'; } };
  const nomFilial = () => { const f = filial(); return window.MFF && window.MFF.esCombo(f) ? window.MFF.txt(f) : f.charAt(0) + f.slice(1).toLowerCase(); };

  // ---------- utilidades ----------
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => (n == null ? '—' : Number(n).toLocaleString('es-PE'));
  const dec = (n) => (n == null ? '—' : Number(n).toLocaleString('es-PE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }));
  const fmtFecha = (iso, conHora = true) => {
    if (!iso) return '—';
    const d = new Date(iso.length === 10 ? iso + 'T12:00:00-05:00' : iso);
    if (isNaN(d)) return esc(iso);
    const op = { timeZone: 'America/Lima', day: '2-digit', month: '2-digit', year: 'numeric' };
    if (conHora && iso.length > 10) Object.assign(op, { hour: '2-digit', minute: '2-digit', hour12: false });
    return d.toLocaleString('es-PE', op);
  };
  const diaCorto = (s) => { const [, m, d] = s.split('-'); return `${d}/${m}`; };
  const pildora = (e) => (e ? `<span class="pildora ${esc(e)}">${esc(e)}</span>` : '—');

  // ---------- acceso ----------
  const token = () => { try { return sessionStorage.getItem(CLAVE_SESION) || ''; } catch { return ''; } };
  const guardarToken = (t) => { try { sessionStorage.setItem(CLAVE_SESION, t); } catch { /* sin almacenamiento */ } };
  const borrarToken = () => { try { sessionStorage.removeItem(CLAVE_SESION); } catch { /* nada */ } };

  class NoAutorizado extends Error {}

  async function api(ruta, t = token()) {
    const r = await fetch(ruta + (ruta.includes('?') ? '&' : '?') + 'filial=' + encodeURIComponent(filial()), { headers: { 'x-portal-token': t } });
    if (r.status === 401) throw new NoAutorizado('No autorizado');
    const datos = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(datos.error || `Error ${r.status}`);
    return datos;
  }

  function pedirAcceso(mensaje = '') {
    acceso.hidden = false; document.getElementById('form-acceso').hidden = false; document.getElementById('form-cambio').hidden = true;
    document.getElementById('error-acceso').textContent = mensaje;
    document.getElementById(document.getElementById('usuario').value ? 'clave' : 'usuario').focus();
  }

  // Ingreso con usuario propio (o la clave general con el usuario en blanco). En el primer ingreso se pide crear la contraseña.
  const $id = (x) => document.getElementById(x);
  let tokenCambio = '';
  async function post(ruta, cuerpo, t) {
    const r = await fetch(ruta, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(t ? { 'x-portal-token': t } : {}) }, body: JSON.stringify(cuerpo) });
    const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `Error ${r.status}`); return d;
  }
  function entrar(d) {
    try { localStorage.setItem('mifibra_usuario', JSON.stringify({ u: d.usuario || '', n: d.nombre || '', f: d.filial || 'TODAS', m: d.modulos || 'TODOS' })); } catch { /* sin almacenamiento */ }
    guardarToken(d.token); location.reload();   // al recargar se aplican la filial y los permisos del usuario en toda la página
  }
  $id('form-acceso').addEventListener('submit', async (ev) => {
    ev.preventDefault(); const btn = ev.submitter || $id('form-acceso').querySelector('button'); btn.disabled = true; $id('error-acceso').textContent = '';
    try {
      const d = await post('/api/usuarios?accion=ingresar', { usuario: $id('usuario').value.trim(), clave: $id('clave').value });
      if (d.cambiar) { tokenCambio = d.token; $id('form-acceso').hidden = true; $id('form-cambio').hidden = false;
        $id('cambio-hola').textContent = `Hola ${d.nombre}, es tu primer ingreso: cambia la contraseña inicial por una tuya (mínimo 6 caracteres).`; $id('nueva').focus(); return; }
      entrar(d);
    } catch (e) { $id('error-acceso').textContent = e.message; } finally { btn.disabled = false; }
  });
  $id('form-cambio').addEventListener('submit', async (ev) => {
    ev.preventDefault(); const n1 = $id('nueva').value, n2 = $id('nueva2').value;
    if (n1 !== n2) { $id('error-cambio').textContent = 'Las contraseñas no coinciden.'; return; }
    try { entrar(await post('/api/usuarios?accion=cambiar', { nueva: n1 }, tokenCambio)); }
    catch (e) { $id('error-cambio').textContent = e.message; }
  });
  // Quién está conectado; el acceso a «Usuarios» se muestra solo a quien ve todas las filiales
  const yo = window.mfUsuario;
  if (yo && yo.n) { const nf = yo.f === 'TODAS' ? 'Todas las filiales' : yo.f.split(',').map(x => x.charAt(0) + x.slice(1).toLowerCase()).join(', '); $id('usr').textContent = `${yo.n} · ${nf}`; $id('usr').hidden = false; }
  $id('adm').hidden = !!(yo && yo.f && yo.f !== 'TODAS');

  const selFilial = document.getElementById('filial');
  selFilial._bloques = true;   // aquí no van los botones de filial-bloques: el menú es compacto (casillas)
  const ponerSel = (v) => { if (![...selFilial.options].some(o => o.value === v)) selFilial.add(new Option(v, v)); selFilial.value = v; };
  ponerSel(filial());
  const cambiarFilial = (v) => { ponerSel(v);
    try { sessionStorage.setItem(CLAVE_FILIAL, v); } catch { /* sin almacenamiento */ }
    cacheIndicadores = null;
    mostrar();
  };
  selFilial.addEventListener('change', () => cambiarFilial(selFilial.value));
  // Varias filiales a la vez: botón con un menú de casillas (Lima, Provincias y cada provincia)
  if (window.MFF) {
    const M = window.MFF, ok = M.ok, pv = M.PROV.filter(ok), opc = [...(ok('LIMA') ? ['LIMA'] : []), ...pv];
    if (opc.length > 1) {
      selFilial.hidden = true;
      const caja = document.createElement('div'); caja.className = 'fmenu';
      caja.innerHTML = '<button type="button" class="fmenu-b" aria-haspopup="true" aria-expanded="false"></button><div class="fmenu-p" hidden role="group" aria-label="Filiales"></div>';
      selFilial.after(caja);
      const btn = caja.querySelector('.fmenu-b'), pan = caja.querySelector('.fmenu-p');
      let S = new Set();
      const etq = () => { btn.textContent = M.txt(filial()) + ' ▾'; btn.title = 'Filial: ' + M.txt(filial()); };
      const pintaP = () => {
        const todas = pv.length && pv.every(p => S.has(p));
        pan.innerHTML = (ok('LIMA') ? `<label><input type="checkbox" data-f="LIMA" ${S.has('LIMA') ? 'checked' : ''}> Lima</label>` : '')
          + (pv.length > 1 ? `<label class="fm-todas"><input type="checkbox" data-f="PROV" ${todas ? 'checked' : ''}> Provincias (todas)</label>` : '')
          + pv.map(p => `<label class="fm-p"><input type="checkbox" data-f="${p}" ${S.has(p) ? 'checked' : ''}> ${M.nom(p)}</label>`).join('')
          + '<div class="fm-acc"><button type="button" class="fm-ok">Ver</button></div>';
      };
      const abrir = (si) => { pan.hidden = !si; btn.setAttribute('aria-expanded', String(si)); if (si) { S = new Set(M.partes(filial())); pintaP(); } };
      btn.addEventListener('click', () => abrir(pan.hidden));
      pan.addEventListener('change', (e) => { const f = e.target.dataset.f; if (!f) return;
        if (f === 'PROV') pv.forEach(p => e.target.checked ? S.add(p) : S.delete(p)); else e.target.checked ? S.add(f) : S.delete(f);
        pintaP(); });
      pan.addEventListener('click', (e) => { if (!e.target.closest('.fm-ok')) return; if (!S.size) return; abrir(false); const v = M.canon([...S]); if (v !== filial()) cambiarFilial(v); etq(); });
      document.addEventListener('click', (e) => { if (!caja.contains(e.target) && !pan.hidden) abrir(false); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pan.hidden) { abrir(false); btn.focus(); } });
      etq();
    }
  }

  document.getElementById('salir').addEventListener('click', () => { borrarToken(); cacheIndicadores = null; pedirAcceso(); });

  async function indicadores(forzar = false) {
    if (!forzar && cacheIndicadores && Date.now() - cacheIndicadores.en < 60_000) return cacheIndicadores.datos;
    const datos = await api('/api/indicadores');
    cacheIndicadores = { datos, en: Date.now() };
    return datos;
  }

  // ---------- configuración de módulos ----------
  const HORARIOS = ['8 - 10', '10 - 12', '12 - 14', '14 - 16', '16 - 18', '18 - 20', 'NO DEFINE'];
  const MODULOS = {
    instalaciones: {
      titulo: 'Instalaciones',
      subtitulo: 'Órdenes de tipo INSTALACION SERVICIO · {FILIAL}',
      pestanas: { param: 'estado', opciones: ['REGISTRADA', 'DESCARGADA', 'OBSERVADA', 'ANULADA'] },
      filtros: [
        { tipo: 'fechas' },
        { param: 'horario', etiqueta: 'Horario de atención', opciones: HORARIOS },
      ],
      columnas: [
        ['nro_os', 'Nro OS'], ['abonado_codigo', 'Abonado'], ['distrito', 'Distrito'], ['categoria', 'Categoría'],
        ['tarifa_servicio', 'Tarifa servicio'], ['fecha_registro', 'F. Recep.', fmtFecha], ['horario_atencion', 'Horario'],
        ['tecnico_ejecutor', 'Técnico ejecutor'], ['contrata', 'Contrata'], ['estado_os', 'Estado Os', pildora],
      ],
      kpis: (i) => [
        ['Registradas (por atender)', num(i.inst_registradas)],
        ['Descargadas', num(i.inst_descargadas)],
        ['Observadas', num(i.inst_observadas)],
        ['Anuladas', num(i.inst_anuladas)],
        ['Efectividad (descargadas ÷ total)', `${dec(i.inst_efectividad_pct)}%`, `${num(i.inst_descargadas)} de ${num(i.inst_total)} órdenes`, 'destacado'],
      ],
    },
    averias: {
      titulo: 'Averías',
      subtitulo: 'Órdenes de tipo SERVICIO TECNICO · {FILIAL}',
      pestanas: { param: 'estado', opciones: ['REGISTRADA', 'DESCARGADA', 'ANULADA'] },
      filtros: [
        { tipo: 'fechas' },
        { param: 'motivo', etiqueta: 'Motivo', opciones: ['SIN SERVICIO DE INTERNET', 'LENTITUD', 'INTERMITENCIA', 'ALCANCE DE WIFI', 'ACCESO ESPECIFICOS', 'PROBLEMA TV', 'PROBLEMAS CON JUEGOS', 'MESA CONTROL DESPACHO', 'RETENCION - CAMPO', 'RETENCIONES', 'OTROS'] },
        { param: 'lugar', etiqueta: 'Lugar avería', opciones: ['RED CLIENTE', 'PRIMERA LINEA', 'SEGUNDA LINEA', 'PLANTA EXTERNA', 'PLANTA INTERNA', 'RETENCION CAMPO'] },
        { param: 'causa', etiqueta: 'Causa', opciones: ['ONU', 'ACOMETIDA', 'PLANTA EXTERNA', 'CONECTORES', 'EXTERNO', 'TÉCNICO', 'NO REPARA', 'CTO', 'COMERCIAL', 'TV', 'PATCH CORD', 'MESH Y ACCESORIOS'] },
      ],
      columnas: [
        ['nro_os', 'Nro OS'], ['abonado_codigo', 'Abonado'], ['distrito', 'Distrito'], ['motivo', 'Motivo'],
        ['lugar_averia', 'Lugar avería'], ['causa', 'Causa', (v, f) => esc([v, f.sub_causa].filter(Boolean).join(' / ') || '—')],
        ['solucion', 'Solución'], ['tecnico_ejecutor', 'Técnico ejecutor'], ['fecha_registro', 'F. Recep.', fmtFecha], ['estado_os', 'Estado Os', pildora],
      ],
      kpis: (i) => [
        ['Registradas (por atender)', num(i.st_registradas)],
        ['Descargadas', num(i.st_descargadas)],
        ['Anuladas', num(i.st_anuladas)],
        ['Tiempo de atención (mediana)', `${dec(i.st_horas_mediana)} h`, `De recepción a fin de atención · promedio ${dec(i.st_horas_promedio)} h`, 'destacado'],
      ],
    },
    observadas: {
      titulo: 'Observadas',
      subtitulo: 'Órdenes con Estado Os OBSERVADA · {FILIAL}',
      pestanas: { param: 'motivo_observacion', opciones: ['COMERCIAL', 'TÉCNICO'] },
      filtros: [
        { param: 'submotivo', etiqueta: 'Submotivo', opciones: ['AUSENTE', 'TV NO COMPATIBLE', 'POSTERGA HASTA NUEVO AVISO', 'NO DESEA - TEMAS ECONOMICOS', 'CONDICIONES INADECUADAS', 'REINGRESO DE ORDEN', 'F.T. ACCESO A CONDOMINIOS', 'CUARTO ALQUILADO', 'NO DESEA - MALA INFORMACION', 'NO DESEA - CONTRATO OTRO OPERADOR', 'NO DESEA - NO SOLICITO SERVICIO', 'EXCESO DE ACOMETIDA', 'FUERA DE COBERTURA', 'NO DESEA - OFERTA DE SU OPERADOR'] },
      ],
      columnas: [
        ['nro_os', 'Nro OS'], ['abonado_codigo', 'Abonado'], ['distrito', 'Distrito'], ['tipo_orden', 'Tipo orden'], ['detalle', 'Detalle'],
        ['motivo_observacion', 'Motivo'], ['submotivo_observacion', 'Submotivo'], ['fecha_observacion', 'Fecha observación', fmtFecha], ['usuario_observacion', 'Usuario'], ['canal_atencion', 'Canal de atención'],
      ],
      kpis: (i) => [
        ['Observadas activas', num(i.obs_total)],
        ['Motivo comercial', num(i.obs_comercial)],
        ['Motivo técnico', num(i.obs_tecnico)],
        ['Submotivo principal', esc(i.obs_submotivo_top || '—'), `${num(i.obs_submotivo_top_n)} órdenes`, 'destacado', true],
      ],
    },
    anuladas: {
      titulo: 'Anuladas',
      subtitulo: 'Órdenes con Estado Os ANULADA · {FILIAL}',
      pestanas: { param: 'tipo', opciones: ['INSTALACION SERVICIO', 'SERVICIO TECNICO', 'OTROS'] },
      filtros: [
        { tipo: 'fechas' },
        { param: 'motivo_anulacion', etiqueta: 'Motivo de anulación', opciones: ['CLIENTE NO DESEA PEDIDO', 'CLIENTE NO DESEA TV NO COMPATIBLE', 'CLIENTE NO SE UBICA', 'ORDEN DUPLICADA', 'CONDICIONES INADECUADAS', 'F.T. ACCESO A CONDOMINIOS', 'CUARTO ALQUILADO', 'REINGRESO ORDEN', 'REPROGRAMACION CONSTANTES', 'CLIENTE AUSENTE', 'FUERA DE COBERTURA', 'NO DESEA - CONTRATO OTRO OPERADOR', 'EXCESO DE ACOMETIDA', 'CASA PRECARIA', 'POSIBLE FRAUDE'] },
        { param: 'canal', etiqueta: 'Canal de atención', opciones: ['ESTABLECIMIENTOS COMERCIALES', 'INTEGRAL DE CAMPO', 'CALL POST VENTA', 'VENTAS INBOUND', 'VENTAS OUTBOUND', 'B2B'] },
      ],
      columnas: [
        ['nro_os', 'Nro OS'], ['tipo_orden', 'Tipo orden'], ['detalle', 'Detalle'], ['abonado_codigo', 'Abonado'], ['distrito', 'Distrito'],
        ['motivo_anulacion', 'Mot. anulación'], ['fecha_mod', 'Fecha mod.', fmtFecha], ['usuario_mod', 'Usuario mod.'], ['canal_atencion', 'Canal de atención'],
      ],
      kpis: (i) => [
        ['Anuladas en el periodo', num(i.anul_total)],
        ['Instalaciones anuladas', num(i.inst_anuladas)],
        ['Motivo principal', esc(i.anul_motivo_top || '—'), `${num(i.anul_motivo_top_n)} órdenes`, '', true],
        ['Tasa de anulación (instalaciones)', i.inst_total ? `${dec((100 * i.inst_anuladas) / i.inst_total)}%` : '—', `${num(i.inst_anuladas)} de ${num(i.inst_total)} instalaciones`, 'destacado'],
      ],
    },
  };

  // Segmentador por canal de atención (común a todos los módulos).
  const CANAL_GRUPOS = [['', 'Todos'], ['IC', 'Integral de campo'], ['VI', 'Ventas inbound'], ['OT', 'Otros canales']];

  const kpiHtml = ([titulo, valor, nota, clase = '', texto = false]) =>
    `<div class="kpi ${clase}"><span class="titulo">${esc(titulo)}</span><span class="valor${texto ? ' texto' : ''}">${valor}</span>${nota ? `<span class="nota">${esc(nota)}</span>` : ''}</div>`;

  const periodo = (i) => (i && i.periodo_desde ? `Registro: ${fmtFecha(i.periodo_desde, false)} – ${fmtFecha(i.periodo_hasta, false)}` : '');

  function pintarActualizado(i) {
    document.getElementById('actualizado').textContent = i && i.actualizado_en ? `Actualizado: ${fmtFecha(i.actualizado_en)} · ScordSoft` : 'Datos: ScordSoft';
  }

  // ---------- vistas ----------
  const ICONOS = {
    instalaciones: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v11h14V9"/><path d="M10 20v-6h4v6"/>',
    averias: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>',
    observadas: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6"/><path d="M12 16.5v.5"/>',
    anuladas: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/>',
    dashboard: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
    facilidades: '<circle cx="12" cy="9" r="2"/><path d="M8.5 5.5a5 5 0 0 0 0 7"/><path d="M15.5 5.5a5 5 0 0 1 0 7"/><path d="M5.6 2.6a9 9 0 0 0 0 12.8"/><path d="M18.4 2.6a9 9 0 0 1 0 12.8"/><path d="M12 11v10"/><path d="M9 21h6"/>',
    speedtest: '<path d="M3.5 17a9 9 0 1 1 17 0"/><path d="m12 13 4-5"/><circle cx="12" cy="13" r="1.5"/><path d="M7 20h10"/>',
  };
  const icono = (k, color) => `<svg aria-hidden="true" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONOS[k]}</svg>`;

  async function vistaInicio() {
    vista.innerHTML = `
      <section class="heroe"><div class="contenedor">
        <span class="etiqueta">Plataforma unificada</span>
        <h1>Toda la operación de campo, <em>en un solo lugar.</em></h1>
        <p>Elige un módulo para revisar instalaciones, averías, órdenes observadas y anuladas, o ver los resultados consolidados.</p>
      </div></section>
      <div class="contenedor"><div class="tarjetas" id="tarjetas"><p class="cargando">Cargando…</p></div></div>`;
    const { indicadores: i } = await indicadores();
    pintarActualizado(i);
    const t = (k, titulo, texto, cifra, destacada = false, href = '#' + k, externo = false) => `
      <a class="tarjeta${destacada ? ' destacada' : ''}" href="${href}"${externo ? ' target="_blank" rel="noopener"' : ''}>
        <span class="icono">${icono(k, destacada ? '#FFFFFF' : '#FF0091')}</span>
        <h2>${titulo}</h2><p>${texto}</p><span class="cifra">${cifra}</span>
        <span class="btn${destacada ? ' oscuro' : ''}">${destacada ? 'Ver resultados →' : externo ? 'Abrir ↗' : 'Ingresar →'}</span>
      </a>`;
    const ver = (k) => !window.mfModulo || window.mfModulo(k === 'facilidades' ? 'fftt' : k);   // ventanas permitidas del usuario
    document.getElementById('tarjetas').innerHTML = [
      t('instalaciones', 'Instalaciones', 'Órdenes de instalación: registradas, descargadas, observadas y anuladas por técnico y distrito.', `${num(i.inst_registradas)} por atender · ${dec(i.inst_efectividad_pct)}% efectividad`),
      t('averias', 'Averías', 'Órdenes de servicio técnico: motivo, lugar de la avería, causa, solución y estado.', `${num(i.st_registradas)} por atender · mediana ${dec(i.st_horas_mediana)} h`),
      t('observadas', 'Observadas', 'Órdenes observadas por motivo comercial o técnico, con su submotivo y usuario.', `${num(i.obs_total)} observadas activas`),
      t('anuladas', 'Anuladas', 'Órdenes anuladas: motivo, usuario que la modificó y canal de atención.', `${num(i.anul_total)} anuladas en el periodo`),
      t('dashboard', 'Dashboard de resultados', 'Indicadores consolidados: efectividad, órdenes por día, estados y ranking de técnicos.', periodo(i), true),
      t('facilidades', 'Facilidades Técnicas', 'Errores de facilidades técnicas por planta (TAP, caja, borne, cintillo) y cintillos duplicados, con la base del portal.', 'Buscador FFTT', false, '/fftt.html', false),
      t('speedtest', 'Speed Test', 'Pruebas de velocidad de cada alta: cobertura de medición, pruebas del cliente o del técnico, AB sin medición y enlaces repetidos.', 'Cobertura de medición por EECC y técnico', false, '/speedtest.html'),
    ].filter((h, i) => ver(['instalaciones', 'averias', 'observadas', 'anuladas', 'dashboard', 'facilidades', 'speedtest'][i])).join('') || '<p class="cargando">Tu usuario no tiene ventanas asignadas. Pide acceso al administrador.</p>';
  }

  async function vistaModulo(clave) {
    const m = MODULOS[clave];
    const estado = { pestana: '', canal: '', filtros: {}, buscar: '', pagina: 1 };
    const filtrosHtml = m.filtros.map((f) => {
      if (f.tipo === 'fechas') {
        return `<label>Registro desde<input type="date" data-filtro="desde"></label><label>Registro hasta<input type="date" data-filtro="hasta"></label>`;
      }
      return `<label>${esc(f.etiqueta)}<select data-filtro="${f.param}"><option value="">Todos</option>${f.opciones.map((o) => `<option>${esc(o)}</option>`).join('')}</select></label>`;
    }).join('');

    vista.innerHTML = `
      <section class="franja"><div class="contenedor">
        <div><a class="volver" href="#inicio">← Volver al inicio</a><h1>${m.titulo}</h1><p id="subtitulo">${m.subtitulo.replace('{FILIAL}', esc(nomFilial()))}</p></div>
        <span class="sello" id="sello">…</span>
      </div></section>
      <div class="contenedor cuerpo">
        <div class="kpis" id="kpis"><p class="cargando">Cargando indicadores…</p></div>
        <div class="panel">
          <div class="pestanas" role="group" aria-label="Filtro principal" id="pestanas">
            ${['', ...m.pestanas.opciones].map((o) => `<button type="button" class="pestana" data-valor="${esc(o)}" aria-pressed="${o === '' ? 'true' : 'false'}">${o === '' ? 'Todas' : esc(o)}</button>`).join('')}
          </div>
          <div class="pestanas canal-grupo" role="group" aria-label="Canal de atención" id="canal-grupo">
            <span class="canal-et">Canal de atención</span>
            ${CANAL_GRUPOS.map(([k, t]) => `<button type="button" class="pestana" data-canal="${k}" aria-pressed="${k === '' ? 'true' : 'false'}">${t}</button>`).join('')}
          </div>
          <form class="filtros" id="filtros">
            ${filtrosHtml}
            <label>Buscar<input type="search" data-filtro="buscar" placeholder="Nro OS, IdOS o abonado" inputmode="numeric"></label>
          </form>
        </div>
        <div class="tabla-caja">
          <table><thead><tr>${m.columnas.map((c) => `<th scope="col">${esc(c[1])}</th>`).join('')}</tr></thead>
          <tbody id="filas"><tr><td class="vacio" colspan="${m.columnas.length}">Cargando órdenes…</td></tr></tbody></table>
          <div class="pie-tabla"><span id="conteo"></span>
            <span class="paginas"><button type="button" id="ant">← Anterior</button><span id="pag"></span><button type="button" id="sig">Siguiente →</button></span>
          </div>
        </div>
      </div>`;

    indicadores().then(({ indicadores: i }) => {
      pintarActualizado(i);
      document.getElementById('sello').textContent = periodo(i) || 'Sin datos';
      document.getElementById('kpis').innerHTML = m.kpis(i).map(kpiHtml).join('');
    }).catch(manejarError);

    const POR_PAGINA = 50;
    let ultimoTotal = 0;
    async function cargarFilas() {
      const p = new URLSearchParams({ modulo: clave, pagina: String(estado.pagina), por_pagina: String(POR_PAGINA) });
      if (estado.pestana) p.set(m.pestanas.param, estado.pestana);
      if (estado.canal) p.set('canal_grupo', estado.canal);
      for (const [k, v] of Object.entries(estado.filtros)) if (v) p.set(k, v);
      if (estado.buscar) p.set('buscar', estado.buscar);
      const cuerpo = document.getElementById('filas');
      cuerpo.innerHTML = `<tr><td class="vacio" colspan="${m.columnas.length}">Cargando órdenes…</td></tr>`;
      try {
        const r = await api(`/api/ordenes?${p}`);
        ultimoTotal = r.total || 0;
        cuerpo.innerHTML = r.filas.length
          ? r.filas.map((f) => `<tr>${m.columnas.map(([campo, , fmt]) => `<td>${fmt ? fmt(f[campo], f) : esc(f[campo] ?? '—')}</td>`).join('')}</tr>`).join('')
          : `<tr><td class="vacio" colspan="${m.columnas.length}">No hay órdenes con estos filtros.</td></tr>`;
        const paginas = Math.max(1, Math.ceil(ultimoTotal / POR_PAGINA));
        document.getElementById('conteo').textContent = `${num(ultimoTotal)} órdenes`;
        document.getElementById('pag').textContent = `Página ${estado.pagina} de ${paginas}`;
        document.getElementById('ant').disabled = estado.pagina <= 1;
        document.getElementById('sig').disabled = estado.pagina >= paginas;
      } catch (e) {
        if (e instanceof NoAutorizado) return manejarError(e);
        cuerpo.innerHTML = `<tr><td class="vacio" colspan="${m.columnas.length}"><span class="aviso">No se pudieron cargar las órdenes: ${esc(e.message)}</span></td></tr>`;
      }
    }

    document.getElementById('pestanas').addEventListener('click', (ev) => {
      const b = ev.target.closest('button[data-valor]');
      if (!b) return;
      document.querySelectorAll('#pestanas .pestana').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      estado.pestana = b.dataset.valor;
      estado.pagina = 1;
      cargarFilas();
    });
    document.getElementById('canal-grupo').addEventListener('click', (ev) => {
      const b = ev.target.closest('button[data-canal]');
      if (!b) return;
      document.querySelectorAll('#canal-grupo .pestana').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      estado.canal = b.dataset.canal;
      estado.pagina = 1;
      cargarFilas();
    });
    let espera;
    document.getElementById('filtros').addEventListener('input', (ev) => {
      const k = ev.target.dataset.filtro;
      if (!k) return;
      if (k === 'buscar') {
        const v = ev.target.value.trim();
        if (v && !/^\d{1,12}$/.test(v)) return; // la búsqueda es por número
        estado.buscar = v;
      } else {
        estado.filtros[k] = ev.target.value;
      }
      estado.pagina = 1;
      clearTimeout(espera);
      espera = setTimeout(cargarFilas, 350);
    });
    document.getElementById('filtros').addEventListener('submit', (ev) => ev.preventDefault());
    document.getElementById('ant').addEventListener('click', () => { estado.pagina -= 1; cargarFilas(); });
    document.getElementById('sig').addEventListener('click', () => { estado.pagina += 1; cargarFilas(); });
    cargarFilas();
  }

  async function vistaDashboard() {
    vista.innerHTML = `
      <section class="franja magenta"><div class="contenedor">
        <div><a class="volver" href="#inicio">← Volver al inicio</a><h1>Dashboard de resultados</h1><p id="subtitulo">Cargando…</p></div>
        <span class="sello" id="sello">…</span>
      </div></section>
      <div class="contenedor cuerpo" id="cuerpo"><p class="cargando">Cargando indicadores…</p></div>`;
    const { indicadores: i, por_dia: dias, por_estado: est, ranking_tecnicos: rk } = await indicadores(true);
    pintarActualizado(i);
    document.getElementById('subtitulo').textContent = `${nomFilial()} · ${num(i.total_ordenes)} órdenes de servicio registradas en el periodo.`;
    document.getElementById('sello').textContent = periodo(i);

    const maxDia = Math.max(1, ...dias.map((d) => Math.max(d.instalaciones, d.servicio_tecnico)));
    const tope = Math.ceil(maxDia / 100) * 100;
    const marcas = [4, 3, 2, 1, 0].map((k) => Math.round((tope * k) / 4));
    const pct = (v) => ((v / tope) * 100).toFixed(1);
    const etiquetas = dias.length ? [0, Math.floor((dias.length - 1) / 3), Math.floor((2 * (dias.length - 1)) / 3), dias.length - 1].map((k) => diaCorto(dias[k].dia)) : [];
    const maxInst = dias.reduce((a, d) => (d.instalaciones > a.instalaciones ? d : a), dias[0] || { instalaciones: 0, dia: '' });
    const maxSt = dias.reduce((a, d) => (d.servicio_tecnico > a.servicio_tecnico ? d : a), dias[0] || { servicio_tecnico: 0, dia: '' });

    const totalEst = Object.values(est).reduce((a, b) => a + b, 0) || 1;
    const ESTADOS = [['DESCARGADA', '#FF0091'], ['REGISTRADA', '#FF8CCB'], ['OBSERVADA', '#6B7280'], ['ANULADA', '#1A1A2E']];

    document.getElementById('cuerpo').innerHTML = `
      <div class="kpis">
        <a class="kpi" href="#instalaciones"><span class="titulo">Instalaciones descargadas</span><span class="valor">${num(i.inst_descargadas)}</span><span class="ver">Ver detalle →</span></a>
        <a class="kpi" href="#averias"><span class="titulo">Servicio técnico descargado</span><span class="valor">${num(i.st_descargadas)}</span><span class="ver">Ver detalle →</span></a>
        <a class="kpi" href="#observadas"><span class="titulo">Observadas</span><span class="valor">${num(i.obs_total)}</span><span class="ver">Ver detalle →</span></a>
        <a class="kpi" href="#anuladas"><span class="titulo">Anuladas</span><span class="valor">${num(i.anul_total)}</span><span class="ver">Ver detalle →</span></a>
        <div class="kpi noche"><span class="titulo">Efectividad instalaciones</span><span class="valor">${dec(i.inst_efectividad_pct)}%</span><span class="nota">${num(i.inst_descargadas)} descargadas de ${num(i.inst_total)} registradas</span></div>
      </div>
      <div class="fila-paneles">
        <div class="panel ancho">
          <div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;align-items:baseline">
            <h2>Órdenes por día de registro</h2>
            <div class="leyenda"><span><i class="muestra" style="background:#FF0091"></i>Instalaciones</span><span><i class="muestra" style="background:#1A1A2E"></i>Servicio técnico</span></div>
          </div>
          <div class="grafico" role="img" aria-label="Instalaciones y servicio técnico registrados por día">
            <div class="eje" aria-hidden="true">${marcas.map((v) => `<span>${v}</span>`).join('')}</div>
            <div class="barras">${dias.map((d) => `<div class="dia" title="${diaCorto(d.dia)} · ${d.instalaciones} instalaciones · ${d.servicio_tecnico} servicio técnico"><i style="height:${pct(d.instalaciones)}%;background:#FF0091"></i><i style="height:${pct(d.servicio_tecnico)}%;background:#1A1A2E"></i></div>`).join('')}</div>
          </div>
          <div class="fechas">${etiquetas.map((e) => `<span>${e}</span>`).join('')}</div>
          <p class="nota-pie">Pico de instalaciones: ${maxInst.dia ? diaCorto(maxInst.dia) : '—'} (${num(maxInst.instalaciones)}). Pico de servicio técnico: ${maxSt.dia ? diaCorto(maxSt.dia) : '—'} (${num(maxSt.servicio_tecnico)}).</p>
        </div>
        <div class="panel">
          <h2>Órdenes por Estado Os</h2>
          <div class="apilada" aria-hidden="true">${ESTADOS.map(([e, c]) => `<div style="width:${(((est[e] || 0) / totalEst) * 100).toFixed(1)}%;background:${c}"></div>`).join('')}</div>
          <div class="reparto">${ESTADOS.map(([e, c]) => `<div><span><i class="muestra" style="background:${c}"></i> ${e.charAt(0) + e.slice(1).toLowerCase()}</span><strong>${num(est[e] || 0)} · ${dec(((est[e] || 0) / totalEst) * 100)}%</strong></div>`).join('')}</div>
          <p class="nota-pie">Incluye todos los tipos de orden del periodo.</p>
        </div>
      </div>
      <div class="tabla-caja">
        <h2 style="margin:0;padding:20px 24px 12px;font-family:Montserrat,sans-serif;font-size:20px">Ranking de técnicos · órdenes descargadas</h2>
        <table><thead><tr><th>#</th><th>Técnico ejecutor</th><th>Contrata</th><th>Instalaciones</th><th>Servicio técnico</th><th>Total</th></tr></thead>
        <tbody>${rk.length ? rk.map((r, k) => `<tr><td style="font-weight:700;color:#FF0091">${k + 1}</td><td>${esc(r.tecnico_ejecutor)}</td><td>${esc(r.contrata || '—')}</td><td><strong>${num(r.instalaciones)}</strong></td><td>${num(r.servicio_tecnico)}</td><td>${num(r.total)}</td></tr>`).join('') : '<tr><td class="vacio" colspan="6">Sin datos</td></tr>'}</tbody></table>
      </div>`;
  }

  // ---------- enrutador ----------
  function manejarError(e) {
    if (e instanceof NoAutorizado) { const habia = token(); borrarToken(); pedirAcceso(habia ? 'Tu sesión expiró. Ingresa de nuevo.' : ''); return; }
    vista.innerHTML = `<div class="contenedor cuerpo"><p class="aviso">No se pudieron cargar los datos: ${esc(e.message)}</p></div>`;
  }

  async function mostrar() {
    let ruta = (location.hash.replace('#', '') || 'inicio').toLowerCase();
    if (window.mfModulo && ruta !== 'inicio' && !window.mfModulo(ruta)) { history.replaceState(null, '', '#inicio'); ruta = 'inicio'; }   // ventana no permitida
    if (ruta === 'averias') { location.replace('/averias.html'); return; } // el módulo Averías y TC vive en su propia página
    if (ruta === 'instalaciones') { location.replace('/instalaciones.html'); return; } // el módulo Instalaciones vive en su propia página
    if (ruta === 'observadas') { location.replace('/observadas.html'); return; } // el módulo Observadas vive en su propia página
    document.querySelectorAll('.nav a[data-ruta]').forEach((a) => a.classList.toggle('activo', a.dataset.ruta === ruta));
    window.scrollTo(0, 0);
    try {
      if (MODULOS[ruta]) await vistaModulo(ruta);
      else if (ruta === 'dashboard') await vistaDashboard();
      else await vistaInicio();
    } catch (e) {
      manejarError(e);
    }
  }

  window.addEventListener('hashchange', mostrar);

  // Arranque: comprueba si hace falta contraseña.
  (async () => {
    try {
      const datos = await api('/api/indicadores');
      cacheIndicadores = { datos, en: Date.now() };
      mostrar();
    } catch (e) {
      if (e instanceof NoAutorizado) pedirAcceso();
      else manejarError(e);
    }
  })();
})();
