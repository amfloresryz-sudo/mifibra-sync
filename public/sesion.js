/* Sesión del portal compartida entre pestañas: la clave del portal se recuerda 12 horas en este navegador
   (antes vivía solo en la pestaña y se perdía al cerrarla o al abrir otra). Se carga antes que el resto de cada página. */
(function () {
  'use strict';
  const TOKEN = 'mifibra_portal_token', FILIAL = 'mifibra_filial', PERSIST = 'mifibra_sesion', USR = 'mifibra_usuario', DURACION = 12 * 3600e3;
  const PROV = ['ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
  let P = null, M = null;   // filiales y ventanas permitidas del usuario (null = todas)
  try {
    const ss = sessionStorage, ls = localStorage;
    let guardada = null; try { guardada = JSON.parse(ls.getItem(PERSIST) || 'null'); } catch { guardada = null; }
    // Vencida (12 h desde que se ingresó la clave): se borra también de esta pestaña y se vuelve a pedir.
    if (guardada && !(guardada.t && guardada.vence > Date.now())) { ls.removeItem(PERSIST); ss.removeItem(TOKEN); guardada = null; }
    if (!guardada && !ss.getItem(TOKEN)) ls.removeItem(USR);
    const actual = ss.getItem(TOKEN);
    if (actual && (!guardada || guardada.t !== actual)) ls.setItem(PERSIST, JSON.stringify({ t: actual, vence: Date.now() + DURACION }));
    else if (!actual && guardada) ss.setItem(TOKEN, guardada.t);
    // La filial elegida también se recuerda entre pestañas.
    const fil = ss.getItem(FILIAL), filG = ls.getItem(FILIAL);
    if (!fil && filG) ss.setItem(FILIAL, filG);
    // Usuario con filial asignada: solo puede ver esa filial (PROVINCIAS = las 7 provincias)
    let usr = null; try { usr = JSON.parse(ls.getItem(USR) || 'null'); } catch { usr = null; }
    window.mfUsuario = usr;
    if (usr && usr.f && !String(usr.f).split(',').includes('TODAS')) {   // una o varias filiales ("LIMA,PIURA")
      const S = new Set(); String(usr.f).split(',').forEach(x => { if (x === 'PROVINCIAS') { S.add('PROVINCIAS'); PROV.forEach(p => S.add(p)); } else S.add(x); });
      if (PROV.every(p => S.has(p))) S.add('PROVINCIAS');
      P = ['LIMA', 'PROVINCIAS', ...PROV].filter(x => S.has(x)); }
    if (usr && usr.m && usr.m !== 'TODOS') M = String(usr.m).split(',');
    // Varias filiales a la vez («LIMA,AREQUIPA»): se permiten si el usuario puede ver cada una
    var filOk = (v) => !P || String(v || '').split(',').every(x => P.includes(x));
    if (P && !filOk(ss.getItem(FILIAL))) { ss.setItem(FILIAL, P[0]); ls.setItem(FILIAL, P[0]); }
    // Mantener sincronizadas sesión y filial cuando la página las cambie.
    const set0 = Storage.prototype.setItem, rem0 = Storage.prototype.removeItem;
    Storage.prototype.setItem = function (k, v) { if (P && k === FILIAL && !filOk(String(v))) v = P[0]; set0.call(this, k, v);
      if (this === ss) { if (k === TOKEN) set0.call(ls, PERSIST, JSON.stringify({ t: String(v), vence: Date.now() + DURACION })); else if (k === FILIAL) set0.call(ls, FILIAL, String(v)); } };
    Storage.prototype.removeItem = function (k) { rem0.call(this, k); if (this === ss && k === TOKEN) { rem0.call(ls, PERSIST); rem0.call(ls, USR); } };
  } catch { /* sin almacenamiento: la sesión queda solo en la pestaña */ }
  window.mfPuede = (k) => !P || P.includes(String(k || '').toUpperCase());
  window.mfModulo = (k) => !M || M.includes(k);
  // Ventana no permitida: se vuelve al inicio (y se ocultan sus accesos del menú)
  const PAG = { 'instalaciones.html': 'instalaciones', 'averias.html': 'averias', 'observadas.html': 'observadas', 'fftt.html': 'fftt', 'speedtest.html': 'speedtest' };
  const pag = PAG[location.pathname.split('/').pop()];
  if (M && pag && !M.includes(pag)) { location.replace('/'); return; }
  const ocultar = () => { if (!M) return; document.querySelectorAll('a[href]').forEach(a => { const h = a.getAttribute('href'), k = PAG[h.replace(/^\//, '').split('#')[0]] || (/#(anuladas|dashboard|observadas|instalaciones|averias)$/.exec(h) || [])[1];
    if (k && !M.includes(k)) a.style.display = 'none'; }); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ocultar); else ocultar();
  // Selectores de filial: se quitan las filiales que el usuario no puede ver
  const podar = () => { if (!P) return; document.querySelectorAll('select#filial').forEach(sel => {
    [...sel.options].forEach(o => { if (!P.includes(o.value)) o.remove(); }); sel.querySelectorAll('optgroup').forEach(g => { if (!g.children.length) g.remove(); });
    if (!String(sel.value).split(',').every(x => P.includes(x))) sel.value = P[0]; }); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', podar); else podar();
})();
