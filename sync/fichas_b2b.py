"""Robot que lee en ScordSoft los datos de contacto de los abonados B2B (para la web B2B).

El reporte «Cronograma de Trabajo» no trae nombre, celular ni dirección del abonado. Esos datos están en la ficha:
  Plataforma Técnica → Registro Ordenes Servicio → Filial + Nº Abonado → Buscar → Consultar → «Datos Del Abonado».

Privacidad (igual que procesar.py): solo se leen abonados del segmento B2B con instalación o avería REGISTRADA, y
los datos van directo a la tabla protegida `b2b_scord_clientes` (solo la ven los usuarios autorizados de la web B2B).
Nunca se imprimen en el registro (los registros de GitHub de este repositorio son públicos) ni se guardan capturas.

Pantallas (leídas de ScordSoft el 13/11/2026):
  Búsqueda  /CTecnico/TECMantOrdenServicio.aspx   (se abre con el postback del menú)
  Ficha     /CTecnico/TECOrdenServicioInstalacionEdicion.aspx   (botón Consultar: solo lectura, nunca se graba)

Uso:
  python -m sync.fichas_b2b                  # lee las fichas pendientes una vez y termina
  python -m sync.fichas_b2b --vigilar 325    # revisa Supabase cada 10 min durante 325 min (turno del robot)
  python -m sync.fichas_b2b --explorar       # reconocimiento: anota SOLO los nombres de los campos (sin datos)
"""
from __future__ import annotations

import argparse
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from playwright.sync_api import Page, sync_playwright

from .descargar import ErrorRobot, _env, iniciar_sesion
from .subir import _cabeceras, _pedir, _url

MENU = "Registro Ordenes Servicio"
BUSQUEDA = "TECMantOrdenServicio.aspx"
GRILLA = "#MainContent_tabForm_tpaReportados_grvResultado"
CAMPOS = {   # campo de la ficha → dato
    "nombre": "#MainContent_txtnombreabonado",
    "domicilio": "#MainContent_txtdireccionabonado",
    "referencia": "#MainContent_txtrefdireccionAbonado",
    "celular1": "#MainContent_viewAbonado_txtCelular1",
    "celular2": "#MainContent_viewAbonado_txtCelular2",
    "celular3": "#MainContent_viewAbonado_txtCelular3",
    "telefono": "#MainContent_viewAbonado_txtTelefono",
}


def pendientes(limite: int = 30) -> list[dict]:
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/rpc/b2b_fichas_pendientes"
    return _pedir("POST", url, "Leer abonados B2B sin ficha", headers=_cabeceras(), json={"p_limite": limite}).json()


def _esperar(page: Page, ms: int = 800) -> None:
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=60_000)
    except Exception:  # noqa: BLE001
        pass


# Inventario SIN valores: id, tipo y la etiqueta de la celda anterior. De los textos solo se anota el largo.
_INVENTARIO = """(raiz) => {
  const r = raiz ? document.querySelector(raiz) : document;
  if (!r) return [];
  const etiqueta = el => { const td = el.closest('td'); let p = td && td.previousElementSibling;
      while (p && !p.innerText.trim()) p = p.previousElementSibling;
      return (p ? p.innerText : '').trim().replace(/\\s+/g, ' ').slice(0, 40); };
  return [...r.querySelectorAll('input, select, textarea, button, a[id]')]
    .filter(e => e.id && e.type !== 'hidden' && e.tagName !== 'A')
    // Nada que esté dentro de una grilla de resultados (ahí las celdas vecinas pueden traer datos del cliente)
    .filter(e => !e.closest('table[id*=grv i], table[id*=gv i], table[id*=grid i], table[id*=gdv i]'))
    .map(e => { const o = {id: e.id, tag: e.tagName.toLowerCase(), tipo: e.type || '', etiqueta: etiqueta(e)};
      if (!/^[A-ZÁÉÍÓÚÑº°\\s().\\/\\-:N0-9]+$/.test(o.etiqueta)) o.etiqueta = '(no es rótulo)';
      if (e.tagName === 'SELECT') o.opciones = e.options.length;
      else if (['text','tel','email','number','textarea',''].includes(e.type)) o.largo = (e.value || '').length;
      else if (['submit','button','image'].includes(e.type)) o.texto = ((e.value || e.title || e.alt || '')).trim().slice(0, 30);
      return o; });
}"""


def _imprimir(titulo: str, items: list[dict]) -> None:
    print(f"--- {titulo}: {len(items)} controles", flush=True)
    for o in items:
        extra = " ".join(f"{k}={o[k]!r}" for k in ("opciones", "largo", "texto") if k in o)
        print(f"   {o['tag']:<8} {o['tipo']:<9} {o['id']:<60} [{o['etiqueta']}] {extra}", flush=True)


def explorar() -> None:
    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    lista = pendientes(5)
    print(f"Abonados B2B sin ficha en Supabase: {len(lista)} (se usa el 1.º para la prueba; no se imprime)", flush=True)
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        page = nav.new_page(viewport={"width": 1500, "height": 1000})
        page.set_default_timeout(60_000)
        iniciar_sesion(page, base)
        _esperar(page)
        # 1) Enlace del menú «Registro Ordenes Servicio» (se lee el href en vez de pasar el mouse por el menú)
        menu = page.evaluate("""(t) => { const n = s => (s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ').trim().toUpperCase();
            const todos = [...document.querySelectorAll('a')];
            const del_menu = todos.filter(a => n(a.innerText || a.textContent) === n(t))
              .map(a => ({href: a.getAttribute('href'), onclick: (a.getAttribute('onclick') || '').slice(0, 200)}));
            const ctec = [...new Set(todos.map(a => a.getAttribute('href') || '').filter(h => /CTecnico/i.test(h)))].slice(0, 40);
            const html = [...document.querySelectorAll('[onclick*=aspx i], [href*=aspx i]')].map(e => (e.getAttribute('href') || e.getAttribute('onclick') || '').slice(0, 150))
              .filter(x => /Orden/i.test(x)).slice(0, 30);
            return {del_menu, ctec, html}; }""", MENU)
        print(f"Menú «{MENU}»: {menu['del_menu']}", flush=True)
        print(f"Enlaces a /CTecnico: {menu['ctec']}", flush=True)
        print(f"Enlaces con «Orden»: {menu['html']}", flush=True)
        # El menú usa __doPostBack (href «#»): se pulsa el mismo enlace y se espera la pantalla nueva.
        with page.expect_navigation(wait_until="networkidle", timeout=60_000):
            page.evaluate("""(t) => { const n = s => (s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ').trim().toUpperCase();
                [...document.querySelectorAll('a')].find(a => n(a.innerText || a.textContent) === n(t)).click(); }""", MENU)
        _esperar(page, 1500)
        print(f"Pantalla de búsqueda: {page.url.split('?')[0].replace(base, '')}", flush=True)
        _imprimir("Pantalla de búsqueda", [o for o in page.evaluate(_INVENTARIO, "form") if o["id"].startswith("MainContent")])
        if not lista:
            return
        c = lista[0]
        # 2) Filial + Nº abonado + fecha inicio de hace un año → Buscar (ids buscados por nombre, no fijos)
        ids = page.evaluate("""() => { const q = re => [...document.querySelectorAll('[id^=MainContent] input, [id^=MainContent] select, input[id^=MainContent], select[id^=MainContent]')].find(e => e.id && re.test(e.id));
            const g = re => (q(re) || {}).id || null;
            return { filial: g(/filial/i), abonado: g(/abonado/i), ini: g(/fecha.*(ini|reg)|ini.*fecha/i), os: g(/n(u|ú)mero.?os|nro.?os|numeroos/i),
                     buscar: ([...document.querySelectorAll('input[type=submit],input[type=button],button,a')].filter(b => /^MainContent/.test(b.id)).find(b => /buscar/i.test(b.value || b.innerText || b.id)) || {}).id || null,
                     consultar: ([...document.querySelectorAll('input[type=submit],input[type=button],button,a')].filter(b => /^MainContent/.test(b.id)).find(b => /consultar/i.test(b.value || b.innerText || b.id)) || {}).id || null }; }""")
        print(f"Campos detectados: {ids}", flush=True)
        if ids.get("filial"):
            valor = page.evaluate("""([id, t]) => { const n = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').trim().toUpperCase();
                const o = [...document.getElementById(id).options].find(o => n(o.text) === n(t)); return o ? o.value : null; }""", [ids["filial"], c["filial"] or ""])
            if valor and page.input_value(f"#{ids['filial']}") != valor and page.is_visible(f"#{ids['filial']}"):
                page.select_option(f"#{ids['filial']}", valor)
                _esperar(page, 1500)
            print(f"Filial elegida: {'sí' if valor else 'NO encontrada'}", flush=True)
        if ids.get("ini"):
            page.evaluate("([id, v]) => { const e = document.getElementById(id); if (e) e.value = v; }",
                          [ids["ini"], (date.today() - timedelta(days=365)).strftime("%d/%m/%Y")])
        if ids.get("abonado"):
            page.fill(f"#{ids['abonado']}", str(c["abonado_codigo"]))
        if not ids.get("buscar"):
            print("No se encontró el botón Buscar.", flush=True)
            return
        page.click(f"#{ids['buscar']}")
        _esperar(page, 2500)
        # 3) Grilla de resultados (pestaña «Reportados»): solo estructura, nunca el texto de las celdas de datos
        grilla = page.evaluate("""() => { const t = [...document.querySelectorAll('table[id*=tpaReportados]')].filter(t => t.rows.length > 1)
              .sort((a, b) => b.rows.length - a.rows.length)[0];
            if (!t) return null;
            const filas = [...t.rows], cab = filas[0];
            const datos = filas.slice(1).filter(r => r.cells.length >= Math.max(3, cab.cells.length - 1));
            const f1 = datos[0] || filas[1];
            return { id: t.id, filas: datos.length, encabezados: [...cab.cells].map(c => c.innerText.trim()).slice(0, 40),
                     controles_fila1: [...f1.querySelectorAll('input,a,select')].map(e => (e.id || '(sin id)') + ':' + (e.type || e.tagName) + (e.getAttribute('onclick') ? ':onclick' : '') + (e.getAttribute('href') && e.getAttribute('href') !== '#' ? ':href=' + e.getAttribute('href').slice(0, 60) : '')).slice(0, 20),
                     fila_onclick: (f1.getAttribute('onclick') || '').slice(0, 120) }; }""")
        print(f"Grilla: {grilla}", flush=True)
        if not grilla or not ids.get("consultar"):
            nav.close()
            return
        dialogos = []
        page.on("dialog", lambda d: (dialogos.append(d.message[:120]), d.accept()))
        # 4) Elegir la 1.ª fila (radio/checkbox; si no, un enlace «Select»; si no, clic en la fila) → Consultar
        elegido = page.evaluate("""(g) => { const t = document.getElementById(g); const f = [...t.rows].slice(1).find(r => r.cells.length >= 3);
            const x = f.querySelector('input[type=radio],input[type=checkbox]');
            if (x) { x.click(); return 'control ' + x.type + ' ' + x.id; }
            const a = f.querySelector('a[href*=Select], a[href*=__doPostBack], input[type=image], input[type=submit]');
            if (a) { a.click(); return 'enlace/botón ' + (a.id || a.getAttribute('href').slice(0, 60)); }
            f.click(); f.cells[1] && f.cells[1].click(); return 'clic en la fila'; }""", grilla["id"])
        print(f"Selección de la fila: {elegido}", flush=True)
        _esperar(page, 2000)
        antes = len(page.context.pages)
        page.click(f"#{ids['consultar']}")
        _esperar(page, 4000)
        paginas = page.context.pages
        print(f"Tras Consultar: ventanas {antes}→{len(paginas)} · "
              f"{[p.url.split('?')[0].replace(base, '') for p in paginas]} · diálogos {dialogos}", flush=True)
        ficha = next((p for p in reversed(paginas) if p.get_by_text("Datos Del Abonado").count()), None)
        if not ficha:
            ficha = next((p for p in reversed(paginas) if "Edicion" in p.url), page)
            try:
                ficha.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:  # noqa: BLE001
                pass
        print(f"Ficha en: {ficha.url.split('?')[0].replace(base, '')} · ¿«Datos Del Abonado»? "
              f"{'sí' if ficha.get_by_text('Datos Del Abonado').count() else 'no'}", flush=True)
        _imprimir("Ficha", [o for o in ficha.evaluate(_INVENTARIO, "form") if o["id"].startswith("MainContent")])
        nav.close()



# ----------------------------------------------------------------------------------------------- lectura real

def _abrir_busqueda(page: Page, base: str) -> None:
    if BUSQUEDA in page.url and page.locator("#MainContent_txtNroAbonado").count():
        return
    if not page.locator("a").filter(has_text=re.compile(r"Registro\s+Ordenes\s+Servicio", re.I)).count():
        page.goto(f"{base}/Default.aspx", wait_until="networkidle")
    with page.expect_navigation(wait_until="networkidle", timeout=60_000):
        page.evaluate("""(t) => { const n = s => (s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ').trim().toUpperCase();
            const a = [...document.querySelectorAll('a')].find(a => n(a.innerText || a.textContent) === n(t)); if (a) a.click(); }""", MENU)
    page.wait_for_selector("#MainContent_txtNroAbonado", timeout=60_000)


def _limpio(v: str | None) -> str | None:
    v = re.sub(r"\s+", " ", (v or "")).strip(" -.")
    return v or None


def _celular(*vals: str | None) -> str | None:
    for v in vals:
        d = re.sub(r"\D", "", v or "")
        if d.startswith("51") and len(d) == 11:
            d = d[2:]
        if len(d) >= 7 and not re.fullmatch(r"0+|9{9}|1{7,}", d):
            return d
    return None


def leer_ficha(page: Page, base: str, c: dict) -> dict:
    """Busca el abonado en Registro Ordenes Servicio, abre Consultar y devuelve sus datos (sin imprimirlos)."""
    _abrir_busqueda(page, base)
    ab = str(int(c["abonado_codigo"]))
    filial = page.evaluate("""(t) => { const n = s => (s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').trim().toUpperCase();
        const o = [...document.querySelector('#MainContent_cboFilial').options].find(o => n(o.text) === n(t)); return o ? o.value : null; }""", c.get("filial") or "")
    if not filial:
        raise ErrorRobot("filial no encontrada en ScordSoft")
    if page.input_value("#MainContent_cboFilial") != filial:   # cambiar la filial recarga la página
        page.select_option("#MainContent_cboFilial", filial)
        _esperar(page, 1500)
        page.wait_for_selector("#MainContent_txtNroAbonado", timeout=60_000)
    page.evaluate("""([ab, ini]) => { const v = (id, x) => { const e = document.getElementById(id); if (e) e.value = x; };
        v('MainContent_txtNroOrdenServicio', ''); v('MainContent_txtNroAbonado', ab); v('MainContent_txtFechaInicio', ini); }""",
        [ab, (date.today() - timedelta(days=365)).strftime("%d/%m/%Y")])
    page.click("#MainContent_btnBuscar")
    page.wait_for_timeout(1500)
    page.wait_for_function("""(g) => document.querySelector(g + ' input[type=radio]') || /TOTAL DE REGISTROS:?\\s*0\\b/.test(document.body.innerText)""",
                           arg=GRILLA, timeout=60_000)
    # Fila cuyo «N° DE ABONADO» es exactamente el abonado (se compara la celda, no el texto de la fila)
    radio = page.evaluate("""([g, ab]) => { const t = document.querySelector(g); if (!t) return null;
        const cab = [...t.rows[0].cells].map(c => c.innerText.trim().toUpperCase()); const i = cab.findIndex(x => /ABONADO/.test(x) && /N/.test(x));
        const f = [...t.rows].slice(1).find(r => r.querySelector('input[type=radio]') && i >= 0 && r.cells[i] && r.cells[i].innerText.trim().replace(/^0+/, '') === ab);
        return f ? f.querySelector('input[type=radio]').id : null; }""", [GRILLA, ab])
    if not radio:
        raise ErrorRobot("el abonado no aparece en Registro Ordenes Servicio")
    page.check(f"#{radio}")
    page.wait_for_timeout(500)
    with page.expect_navigation(wait_until="networkidle", timeout=60_000):
        page.click("#MainContent_btnConsultar")
    page.wait_for_selector(CAMPOS["nombre"], timeout=60_000)
    num = page.input_value("#MainContent_txtNumAbonado").strip().lstrip("0") if page.locator("#MainContent_txtNumAbonado").count() else ""
    if num != ab:
        raise ErrorRobot("la ficha abierta no es del abonado buscado")
    v = {k: (page.input_value(sel) if page.locator(sel).count() else "") for k, sel in CAMPOS.items()}
    domicilio, ref = _limpio(v["domicilio"]), _limpio(v["referencia"])
    return {
        "abonado_codigo": int(ab),
        "razon_social": _limpio(v["nombre"]),
        "telefono": _celular(v["celular1"], v["celular2"], v["celular3"], v["telefono"]),
        "direccion": domicilio if not ref or (domicilio and ref.upper() in domicilio.upper()) else f"{domicilio or ''} (Ref.: {ref})".strip(),
    }


def guardar(fila: dict) -> None:
    # Los vacíos no se envían: así nunca se borra lo que un usuario escribió a mano en la web B2B
    fila = {**{k: v for k, v in fila.items() if v is not None}, "actualizado": datetime.now(timezone.utc).isoformat()}
    _pedir("POST", _url("b2b_scord_clientes"), "Guardar datos de cliente B2B",
           headers=_cabeceras({"Prefer": "resolution=merge-duplicates,return=minimal"}),
           params={"on_conflict": "abonado_codigo"}, json=fila)


def procesar(limite: int = 30, probar: bool = False) -> int:
    lista = pendientes(limite)
    if not lista:
        return 0
    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    ok = 0
    print(f"{datetime.now(timezone(timedelta(hours=-5))):%H:%M} · {len(lista)} abonado(s) B2B sin datos de contacto: se leen sus fichas", flush=True)
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        page = nav.new_page(viewport={"width": 1500, "height": 1000})
        page.set_default_timeout(60_000)
        iniciar_sesion(page, base)
        _esperar(page)
        for n, c in enumerate(lista, 1):
            try:
                fila = leer_ficha(page, base, c)
                if not probar:
                    guardar(fila)
                ok += 1
                # Nunca se imprimen los datos: solo si se encontraron
                print(f"   {n}/{len(lista)} · ficha leída (nombre {'sí' if fila['razon_social'] else 'no'}, "
                      f"celular {'sí' if fila['telefono'] else 'no'}, dirección {'sí' if fila['direccion'] else 'no'})", flush=True)
            except Exception as e:  # noqa: BLE001 - una ficha fallida no frena las demás
                msg = str(e).splitlines()[0][:120] if isinstance(e, ErrorRobot) else type(e).__name__
                print(f"   {n}/{len(lista)} · no se pudo leer: {msg}", flush=True)
                try:   # se marca para no reintentar en cada vuelta (se vuelve a intentar al día siguiente)
                    if not probar:
                        guardar({"abonado_codigo": int(c["abonado_codigo"])})
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.goto(f"{base}/Default.aspx", wait_until="networkidle")
                except Exception:  # noqa: BLE001
                    pass
        nav.close()
    return ok

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explorar", action="store_true", help="solo anota los nombres de los campos (sin datos)")
    ap.add_argument("--vigilar", type=int, default=0, help="minutos revisando Supabase cada 10 min")
    ap.add_argument("--probar", type=int, default=0, metavar="N", help="lee N fichas SIN guardar nada (para revisar)")
    a = ap.parse_args()
    if a.explorar:
        explorar()
        return
    if a.probar:
        print(f"Modo prueba: se leen hasta {a.probar} fichas y NO se guarda nada.", flush=True)
        procesar(a.probar, probar=True)
        return
    fin = time.time() + a.vigilar * 60
    while True:
        h = datetime.now(timezone(timedelta(hours=-5))).hour
        if 7 <= h <= 22:   # solo en horario de oficina de ScordSoft
            try:
                procesar()
            except Exception as e:  # noqa: BLE001 - el vigía no debe caerse por un error pasajero
                print(f"Fichas B2B: {type(e).__name__}: {str(e).splitlines()[0][:150] if str(e) else ''}", flush=True)
        if time.time() + 600 > fin:
            break
        time.sleep(600)


if __name__ == "__main__":
    main()
