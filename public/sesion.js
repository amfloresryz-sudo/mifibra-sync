/* Sesión del portal compartida entre pestañas: la clave del portal se recuerda 12 horas en este navegador
   (antes vivía solo en la pestaña y se perdía al cerrarla o al abrir otra). Se carga antes que el resto de cada página. */
(function () {
  'use strict';
  const TOKEN = 'mifibra_portal_token', FILIAL = 'mifibra_filial', PERSIST = 'mifibra_sesion', DURACION = 12 * 3600e3;
  try {
    const ss = sessionStorage, ls = localStorage;
    let guardada = null; try { guardada = JSON.parse(ls.getItem(PERSIST) || 'null'); } catch { guardada = null; }
    // Vencida (12 h desde que se ingresó la clave): se borra también de esta pestaña y se vuelve a pedir.
    if (guardada && !(guardada.t && guardada.vence > Date.now())) { ls.removeItem(PERSIST); ss.removeItem(TOKEN); guardada = null; }
    const actual = ss.getItem(TOKEN);
    if (actual && (!guardada || guardada.t !== actual)) ls.setItem(PERSIST, JSON.stringify({ t: actual, vence: Date.now() + DURACION }));
    else if (!actual && guardada) ss.setItem(TOKEN, guardada.t);
    // La filial elegida también se recuerda entre pestañas.
    const fil = ss.getItem(FILIAL), filG = ls.getItem(FILIAL);
    if (!fil && filG) ss.setItem(FILIAL, filG);
    // Mantener sincronizadas sesión y filial cuando la página las cambie.
    const set0 = Storage.prototype.setItem, rem0 = Storage.prototype.removeItem;
    Storage.prototype.setItem = function (k, v) { set0.call(this, k, v);
      if (this === ss) { if (k === TOKEN) set0.call(ls, PERSIST, JSON.stringify({ t: String(v), vence: Date.now() + DURACION })); else if (k === FILIAL) set0.call(ls, FILIAL, String(v)); } };
    Storage.prototype.removeItem = function (k) { rem0.call(this, k); if (this === ss && k === TOKEN) rem0.call(ls, PERSIST); };
  } catch { /* sin almacenamiento: la sesión queda solo en la pestaña */ }
})();
