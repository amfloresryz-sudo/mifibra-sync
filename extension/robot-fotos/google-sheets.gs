/**
 * Recibe los resultados del "Robot validación de fotos" y los guarda en esta hoja.
 * Instalación: Extensiones → Apps Script → pegar este código → Implementar → Nueva implementación →
 *   Tipo: Aplicación web · Ejecutar como: Yo · Quién tiene acceso: Cualquier persona → Implementar.
 * Copia la URL que termina en /exec y pégala en el robot (⚙ → Google Sheets → Guardar).
 */
const HOJA = 'Validaciones';
const COLUMNAS = [
  ['fecha_revision', 'Fecha revisión'], ['revisado_por', 'Revisado por'], ['ab', 'AB'], ['os', 'OS'],
  ['fecha_ejecucion', 'Fecha ejecución'], ['tecnico', 'Técnico'], ['n_fotos', 'N° fotos'], ['resultado', 'Resultado'],
  ['perdida_db', 'Pérdida caja–cliente (dB)'], ['errores', 'Fotos con error / falta'], ['resumen', 'Resumen'], ['ia', 'Revisado con'],
  ['F1', 'F1 Fachada'], ['F2', 'F2 Potencia'], ['F3', 'F3 NAP cerrada'], ['F4', 'F4 Router'], ['F5', 'F5 DNI/boletín'],
  ['F6', 'F6 Borne/cintillo'], ['F7', 'F7 Speedtest cliente'], ['F8', 'F8 Speedtest técnico'], ['F9', 'F9 WiFi/remoto'],
  ['F10', 'F10 MESH'], ['F11', 'F11 Mapa CTO'], ['F12', 'F12 Parámetros'],
];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const d = JSON.parse(e.postData.contents);
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(HOJA);
    if (!sh) sh = ss.insertSheet(HOJA);
    if (sh.getLastRow() === 0) {
      sh.appendRow(COLUMNAS.map((c) => c[1]));
      sh.getRange(1, 1, 1, COLUMNAS.length).setFontWeight('bold').setBackground('#1f4e9c').setFontColor('#ffffff');
      sh.setFrozenRows(1);
    }
    sh.appendRow(COLUMNAS.map((c) => (d[c[0]] === undefined || d[c[0]] === null ? '' : d[c[0]])));
    const fila = sh.getLastRow();
    const color = d.resultado === 'OK' ? '#e6f6ea' : d.resultado === 'OBSERVADA' ? '#fde8e8' : null;
    if (color) sh.getRange(fila, 8).setBackground(color);
    return ContentService.createTextOutput('ok');
  } catch (err) {
    return ContentService.createTextOutput('error: ' + err);
  } finally {
    lock.releaseLock();
  }
}

// Para probar desde el editor: Ejecutar → prueba (agrega una fila de ejemplo)
function prueba() {
  doPost({ postData: { contents: JSON.stringify({ fecha_revision: new Date().toLocaleString(), ab: 'PRUEBA', os: '00000000', resultado: 'OK', resumen: 'Fila de prueba' }) } });
}
