"""Robot que escribe en ScordSoft los comentarios que las EECC dejan en «Mis pendientes».

Hace lo mismo que se hace a mano:
  1. Plataforma Técnica → Agendamiento de Ordenes (TECProgramacionOrdenes.aspx).
  2. Filial de la OS, Nº OS, Buscar.
  3. Icono «ING. VISITA.» de la fila → ventana «Edición Observación» → OBSERVACIONES → Grabar.
     (queda en OBS VISITA TÉCNICO, con la fecha y hora que pone ScordSoft).

Para no duplicar ni dejar nada a medias:
  - Antes de grabar mira el historial «H. VISITAS» de la OS: si el comentario ya está, solo lo marca como escrito.
  - Después de grabar vuelve a mirar el historial: solo si lo encuentra lo marca «escrito» en Supabase.
  - Si grabó pero no lo puede confirmar, lo marca «error» (no reintenta, para no escribirlo dos veces).
  - Nunca toca los botones de editar o borrar del historial.

Uso:
  python -m sync.comentarios_scord                  # escribe los pendientes una vez y termina
  python -m sync.comentarios_scord --vigilar 300    # revisa Supabase cada minuto durante 300 min (turno del robot)
  python -m sync.comentarios_scord --probar         # hace todo menos pulsar Grabar (para revisar)

Variables de entorno: las de descargar.py (SCORDSOFT_*) y las de subir.py (SUPABASE_URL, SUPABASE_SERVICE_KEY).
"""
from __future__ import annotations

import argparse
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from .descargar import ErrorRobot, _captura, _env, iniciar_sesion
from .subir import _cabeceras, _pedir, _url

PANTALLA = "/CTecnico/TECProgramacionOrdenes.aspx"
GRILLA = "#MainContent_tabForm_tpaReportados_grvOrdenServicioProgramacion"
CAPTURAS = Path("diagnostico_comentarios")
MAX_TEXTO = 480
MAX_INTENTOS = 3


# ----------------------------------------------------------------------------------------------- Supabase

def pendientes(limite: int = 20) -> list[dict]:
    r = _pedir("GET", _url("eecc_comentarios"), "Leer comentarios pendientes", headers=_cabeceras(),
               params={"select": "id,os,ab,filial,eecc,usuario,nombre_usuario,texto,scord_intentos,creado_en",
                       "scord_estado": "eq.pendiente", "order": "creado_en.asc", "limit": str(limite)})
    return r.json()


def marcar(cid: int, **campos) -> None:
    _pedir("PATCH", _url("eecc_comentarios"), "Marcar comentario", headers=_cabeceras({"Prefer": "return=minimal"}),
           params={"id": f"eq.{cid}"}, json=campos)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------------------------- texto

# Mismas reglas que el portal (Gestión / 1er contacto): el comentario trae agenda si tiene día Y franja/hora.
RE_FEC = re.compile(r"\b\d{1,2}\s*[/.]\s*(?:\d{1,2}|1O)\b|\b(?:LUNES|MARTES|MIERCOLES|JUEVES|VIERNES|SABADO|DOMINGO|HOY|MANANA)\b"
                    r"|\b\d{1,2}\s+DE\s+(?:ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SE?TIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)\b")
RE_FRA = re.compile(r"\b\d{1,2}(?:\s*:\s*\d{2})?\s*(?:AM|PM|HRS?)?\s*(?:A|-|AL|HASTA)\s*\d{1,2}(?:\s*:\s*\d{2})?\s*(?:AM|PM|HRS?)?\b"
                    r"|\bA\s+LAS\s+\d{1,2}|\b\d{1,2}\s*(?::\s*\d{2})?\s*(?:AM|PM)\b|\b(?:RANGO|FRANJA)\s+(?:DE\s+)?\d")
RE_SELLO = re.compile(r"#\s*\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*#")


def cuerpo(c: dict) -> str:
    """Solo el comentario de la EECC (sin # ni %: el portal los usa para el 1er contacto y el técnico)."""
    t = re.sub(r"[#%<>]", " ", c["texto"])
    return re.sub(r"\s+", " ", t).strip()


def con_agenda(texto: str) -> bool:
    t = _norm(texto)
    return bool(RE_FEC.search(t) and RE_FRA.search(t))


def texto_scord(c: dict, hist: str = "", ahora: datetime | None = None) -> str:
    """Comentario tal cual. Si es el 1er comentario con agenda de la OS (el historial aún no tiene ningún
    #dd/mm/aaaa hh:mm#), va precedido del sello #dd/mm/aaaa hh:mm# con la hora de Lima en que se graba."""
    t = cuerpo(c)
    if con_agenda(t) and not RE_SELLO.search(hist or ""):
        ahora = ahora or datetime.now(timezone(timedelta(hours=-5)))
        t = f"#{ahora:%d/%m/%Y %H:%M}# {t}"
    return t[:MAX_TEXTO]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().upper()


# ----------------------------------------------------------------------------------------------- ScordSoft

def _postback(page: Page, accion, espera: str | None = None, timeout: int = 60_000) -> None:
    """Ejecuta una acción que dispara un postback de ASP.NET y espera a que la página quede quieta."""
    accion()
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle", timeout=timeout)
    if espera:
        page.wait_for_selector(espera, state="visible", timeout=timeout)


def buscar_os(page: Page, base: str, c: dict):
    """Deja en la grilla la fila de la OS y la devuelve (verifica que sea del mismo abonado).

    Los filtros (estado, fecha, Nº OS) se escriben directo en los campos, sin disparar «change»: algunos combos de
    ScordSoft recargan la página al cambiar y borraban lo escrito (así es como funciona a mano y probado en el navegador).
    Después de «Buscar» se espera a que la grilla muestre la OS o diga «TOTAL DE REGISTROS: 0»."""
    page.goto(f"{base}{PANTALLA}", wait_until="networkidle")
    page.wait_for_selector("#MainContent_txtNumeroOS", timeout=60_000)
    filial = _norm(c["filial"])
    valor = page.evaluate(
        """(t) => { const n = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toUpperCase().trim();
                    const o = [...document.querySelector('#MainContent_cboFilial').options].find(o => n(o.text) === t);
                    return o ? o.value : null; }""", filial)
    if not valor:
        raise ErrorRobot(f"La filial {c['filial']} no está en ScordSoft.")
    if page.input_value("#MainContent_cboFilial") != valor:   # cambiar la filial recarga la página (postback)
        page.select_option("#MainContent_cboFilial", valor)
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_selector("#MainContent_txtNumeroOS", timeout=60_000)
        if page.input_value("#MainContent_cboFilial") != valor:
            raise ErrorRobot(f"No se pudo elegir la filial {c['filial']} en Agendamiento de Ordenes.")
    os_ = str(c["os"])
    hace_un_anio = (date.today() - timedelta(days=365)).strftime("%d/%m/%Y")
    visto = ""
    for estado in ("001", "0"):   # primero REGISTRADA; si ya cambió de estado, en todas y con un año de fechas
        page.evaluate(
            """([os, est, ini]) => { const v = (id, x) => { const e = document.getElementById(id); if (e) e.value = x; };
                v('MainContent_cboEstadoOS', est); v('MainContent_txtAbonado', ''); v('MainContent_txtNumeroOS', os);
                if (ini) v('MainContent_txtFechaRegistroIni', ini); }""", [os_, estado, hace_un_anio if estado == "0" else None])
        page.click("#MainContent_btnBuscar")
        page.wait_for_timeout(1500)
        try:
            page.wait_for_function(
                """([g, os]) => { const t = document.querySelector(g);
                    if (t && [...t.querySelectorAll('td')].some(td => td.innerText.trim() === os)) return true;
                    return /TOTAL DE REGISTROS:?\\s*0\\b/.test(document.body.innerText); }""", arg=[GRILLA, os_], timeout=60_000)
        except Exception:  # noqa: BLE001 - se revisa abajo qué quedó en pantalla
            pass
        # Fila cuya celda NROOS es exactamente la OS (se compara celda por celda: el texto de la fila viene pegado)
        r = page.evaluate(
            """([g, os]) => { const t = document.querySelector(g), filas = t ? [...t.querySelectorAll('tr')].filter(tr => tr.querySelector('input[id*=ibtnLogIngvisita]')) : [];
                const m = (document.body.innerText.match(/TOTAL DE REGISTROS:?\\s*\\d+/) || [''])[0];
                return { ids: filas.filter(tr => [...tr.cells].some(td => td.innerText.trim() === os)).map(tr => tr.querySelector('input[id*=ibtnLogIngvisita]').id),
                         n: filas.length, tot: m, os: document.getElementById('MainContent_txtNumeroOS')?.value || '',
                         fil: document.getElementById('MainContent_cboFilial')?.value || '', est: document.getElementById('MainContent_cboEstadoOS')?.value || '',
                         ej: filas.slice(0, 2).map(tr => [...tr.cells].slice(1, 5).map(td => td.innerText.trim()).join('/')).join(' | ') }; }""", [GRILLA, os_])
        visto = f"[estado {estado}: {r['tot'] or 'sin total'} · filas {r['n']} · Nº OS en pantalla «{r['os']}» · filial {r['fil']} · estado {r['est']}{' · ' + r['ej'] if r['ej'] else ''}]"
        print(f"   búsqueda {visto}", flush=True)
        ids = r["ids"]
        if len(ids) == 1:
            fila = page.locator(f"{GRILLA} tr").filter(has=page.locator(f"#{ids[0]}"))
            texto = fila.inner_text()
            if c.get("ab") and not re.search(rf"\b0*{int(c['ab'])}\b", texto):
                raise ErrorRobot(f"La OS {c['os']} en ScordSoft no es del AB {c['ab']}.")
            return fila
        if len(ids) > 1:
            raise ErrorRobot(f"La búsqueda de la OS {c['os']} trajo {len(ids)} filas.")
    raise ErrorRobot(f"No se encontró la OS {c['os']} en Agendamiento de Ordenes. {visto}")


def historial(page: Page, fila) -> str:
    """Texto del historial «H. VISITAS» de la OS (solo lectura) y cierra la ventana."""
    _postback(page, lambda: fila.locator("input[id*=ibtnLogVisitasTecnico]").click(), "#MainContent_pnlLogVisitasTecnico")
    t = page.inner_text("#MainContent_grvListadoVisitaTecnica") if page.locator("#MainContent_grvListadoVisitaTecnica").count() else ""
    page.click("#divCerrarLogVisitasTecnico")
    page.wait_for_timeout(500)
    return t


def ya_esta(hist: str, texto: str) -> bool:
    return _norm(RE_SELLO.sub(" ", texto)).strip()[:120] in _norm(hist)


def escribir(page: Page, base: str, c: dict, probar: bool) -> str:
    """Devuelve 'escrito' (grabado y confirmado), 'ya_estaba' o 'prueba'."""
    fila = buscar_os(page, base, c)
    hist = historial(page, fila)
    if ya_esta(hist, cuerpo(c)):
        return "ya_estaba"
    hora = None
    if c.get("creado_en"):   # el sello lleva la hora en que la EECC escribió el comentario (hora de Lima)
        hora = datetime.fromisoformat(str(c["creado_en"]).replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=-5)))
    texto = texto_scord(c, hist, hora)
    print(f"   se graba: {texto}", flush=True)
    fila = buscar_os(page, base, c)   # tras cerrar la ventana la grilla puede haberse redibujado
    _postback(page, lambda: fila.locator("input[id*=ibtnLogIngvisita]").click(), "#MainContent_pnlVisitatecnico")
    if not page.input_value("#MainContent_hfregIdOSVisita"):
        raise ErrorRobot("La ventana «Edición Observación» no cargó la OS.")
    if not page.input_value("#MainContent_txtRegFechaVisita").strip():
        page.fill("#MainContent_txtRegFechaVisita", datetime.now(timezone(timedelta(hours=-5))).strftime("%d/%m/%Y"))
    if not re.match(r"^\d{2}:\d{2}$", page.input_value("#MainContent_txtReghoraVisita")):
        page.fill("#MainContent_txtReghoraVisita", datetime.now(timezone(timedelta(hours=-5))).strftime("%H:%M"))
    page.fill("#MainContent_txtRegObservaciones", texto)
    if probar:
        _captura(page, CAPTURAS, f"prueba_{c['os']}")
        page.click("#divCerrarVisitatecnico")
        return "prueba"
    avisos: list[str] = []
    page.once("dialog", lambda d: (avisos.append(d.message), d.accept()))
    _postback(page, lambda: page.click("#MainContent_btnAceptarVisita"))
    msg = (page.inner_text("#MainContent_lblregVisitaTecnica") if page.locator("#MainContent_lblregVisitaTecnica").count() else "").strip()
    if msg or avisos:
        print(f"   ScordSoft dijo: {msg or ''} {' '.join(avisos)}".rstrip(), flush=True)
    fila = buscar_os(page, base, c)
    if ya_esta(historial(page, fila), texto):
        return "escrito"
    raise ErrorRobot("GRABADO_SIN_CONFIRMAR: se pulsó Grabar pero el comentario no aparece en el historial. "
                     + (msg or " ".join(avisos)))


# ----------------------------------------------------------------------------------------------- ciclo

def procesar(lista: list[dict], probar: bool) -> None:
    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        page = nav.new_page(viewport={"width": 1400, "height": 1000})
        page.set_default_timeout(60_000)
        iniciar_sesion(page, base)
        for c in lista:
            print(f"Comentario {c['id']} · OS {c['os']} · AB {c.get('ab')} · {c['eecc']}", flush=True)
            try:
                r = escribir(page, base, c, probar)
                print(f"   → {r}", flush=True)
                if r in ("escrito", "ya_estaba"):
                    marcar(c["id"], scord_estado="escrito", scord_en=_ahora(), scord_error=None,
                           scord_intentos=(c.get("scord_intentos") or 0) + 1)
            except Exception as e:  # noqa: BLE001 - un comentario fallido no detiene los demás
                _captura(page, CAPTURAS, f"error_{c['os']}_{int(time.time())}")
                n = (c.get("scord_intentos") or 0) + 1
                definitivo = "GRABADO_SIN_CONFIRMAR" in str(e) or n >= MAX_INTENTOS
                print(f"   ✗ {e}", flush=True)
                if not probar:
                    marcar(c["id"], scord_intentos=n, scord_error=str(e)[:300],
                           **({"scord_estado": "error"} if definitivo else {}))
        nav.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vigilar", type=int, default=0, help="minutos revisando Supabase cada minuto")
    ap.add_argument("--probar", action="store_true", help="todo menos pulsar Grabar")
    a = ap.parse_args()
    fin = time.time() + a.vigilar * 60
    while True:
        try:
            lista = pendientes()
            if lista:
                print(f"{datetime.now(timezone(timedelta(hours=-5))):%H:%M} · {len(lista)} comentario(s) de EECC por escribir en ScordSoft", flush=True)
                procesar(lista, a.probar)
        except Exception as e:  # noqa: BLE001 - el vigía no debe caerse por un error pasajero
            print(f"Comentarios EECC: {e}", flush=True)
        if time.time() + 60 > fin:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
