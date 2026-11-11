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

Agenda (si el comentario trae día y franja, p. ej. «cliente agenda 12/11 de 8 a 10»):
  4. Mismo AB en Agendamiento de Ordenes (estado REGISTRADA): se marcan TODAS sus OS registradas → «Asignar Tecnico» →
     «Agendar Orden de servicio»: FECHA ATENCIÓN, HORARIO ATENCION OS (8 - 10 … 18 - 20) y OBSERVACIÓN; el técnico se deja
     el que ya tiene la OS → Grabar. Luego vuelve a buscar y confirma que la OS quedó con esa fecha y franja.
  - Manda el ÚLTIMO comentario con agenda de la OS (los anteriores quedan «reemplazado»); sin agenda no se toca nada.
  - No se agenda si la fecha ya pasó, si la OS ya no está registrada o si no tiene técnico; si ya tenía esa fecha y
    franja, queda «igual» sin grabar.

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
               params={"select": "id,os,ab,filial,eecc,usuario,nombre_usuario,texto,scord_intentos,creado_en,ag_estado",
                       "scord_estado": "eq.pendiente", "order": "creado_en.asc", "limit": str(limite)})
    return r.json()


def hay_agenda_mas_nueva(c: dict) -> bool:
    """¿Hay otro comentario más nuevo para la misma OS que también trae agenda? (manda el último)."""
    r = _pedir("GET", _url("eecc_comentarios"), "Leer comentarios de la OS", headers=_cabeceras(),
               params={"select": "id,texto,creado_en", "os": f"eq.{c['os']}", "creado_en": f"gt.{c['creado_en']}", "order": "creado_en.asc"})
    return any(agenda_de(x["texto"], _dia_lima(x["creado_en"])) for x in r.json())


def _dia_lima(iso: str) -> date:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=-5))).date()


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


# ----------------------------------------------------------------------------------------------- agenda del comentario

FRANJAS = ["8 - 10", "10 - 12", "12 - 14", "14 - 16", "16 - 18", "18 - 20"]
_DIAS_SEM = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
_MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SETIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
# día: 12/11 · 12.11 · 12 DE NOVIEMBRE · HOY · MAÑANA · PASADO MAÑANA · LUNES… (con número opcional: «MAÑANA 09», «JUEVES 12»)
_RE_DIA = re.compile(r"\b(?P<d>\d{1,2})\s*[/.]\s*(?P<m>\d{1,2})(?:\s*[/.]\s*\d{2,4})?\b"
                     r"|\b(?P<d3>\d{1,2})\s*-\s*(?P<m3>\d{1,2})\b(?=\s*(?:/\s*\d{2,4}\s*)?(?:DE|EN|A\s+LAS|RANGO|HORARIO|ENTRE)\b)"
                     r"|\b(?P<d2>\d{1,2})\s+DE\s+(?P<mes>ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SE?TIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)\b"
                     r"|\b(?P<rel>PASADO\s+MANANA|HOY|MANANA)\b(?:\s+(?:\w+\s+)?(?P<dr>\d{1,2})\b(?!\s*(?::|AM|PM|A\b|-|HRS?)))?"
                     r"|\b(?P<sem>LUNES|MARTES|MIERCOLES|JUEVES|VIERNES|SABADO|DOMINGO)\b(?:\s+(?P<ds>\d{1,2})\b(?!\s*(?::|AM|PM|A\b|-|HRS?)))?")
_H = r"(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(AM|PM|A\.?\s?M\.?|P\.?\s?M\.?|HRS?|[AP](?=\s+A\b))?"
_RE_RANGO = re.compile(r"\b" + _H + r"\s*(?:A|-|AL|HASTA)\s*" + _H + r"(?![\d/])")
_RE_HORA = re.compile(r"\bA\s+LAS\s+" + _H + r"|\b(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(AM|PM)\b|\b(?:RANGO|FRANJA|HORARIO)\s+(?:DE\s+)?" + _H)


def _hora24(h: int, mer: str | None, mer2: str | None = None) -> int:
    mer = (mer or mer2 or "").replace(".", "").replace(" ", "")
    if mer.startswith("P") and h < 12:
        return h + 12
    if not mer.startswith("A") and h < 8:   # sin AM/PM, «de 2 a 4» es de la tarde
        return h + 12
    return h


def _franja(h: int) -> str | None:
    """Franja de 2 h que contiene la hora de inicio (9 → 8 - 10, 15 → 14 - 16); fuera de 8:00–19:59 no hay franja."""
    return FRANJAS[(h - 8) // 2] if 8 <= h < 20 else None


def _franja_en(t: str, desde: int = 0) -> str | None:
    """Primera franja u hora que aparece en el texto desde la posición dada."""
    r, h = _RE_RANGO.search(t, desde), _RE_HORA.search(t, desde)
    m = min([x for x in (r, h) if x], key=lambda x: x.start(), default=None)
    if not m:
        return None
    if m.re is _RE_RANGO:
        h1 = int(m[1])
        if h1 > 23 or int(m[4]) > 23:
            return None
        h2 = _hora24(int(m[4]), m[6])
        if m[3]:
            return _franja(_hora24(h1, m[3]))
        # sin AM/PM en la 1.ª hora: toma el de la 2.ª solo si cuadra («4-6pm» = 16; «10-12pm» = 10; «12-2pm» = 12)
        return _franja(h1 + 12 if h1 < 12 and (h1 < 8 or (m[6] or "").startswith("P") and h1 + 12 <= h2) else h1)
    g = [x for x in m.groups()]
    for i in (0, 3, 6):   # «A LAS h», «h AM/PM», «RANGO DE h»
        if g[i]:
            return _franja(_hora24(int(g[i]), g[i + 2])) if int(g[i]) <= 23 else None
    return None


def _dia(m: re.Match, ref: date) -> date | None:
    try:
        if m["d"]:
            d, mes = int(m["d"]), int(m["m"])
        elif m["d3"]:   # «10-11 DE 4 A 6»: día-mes con guion, solo del mes en curso o del siguiente
            d, mes = int(m["d3"]), int(m["m3"])
            if mes not in (ref.month, ref.month % 12 + 1):
                return None
        elif m["d2"]:
            d, mes = int(m["d2"]), _MESES.index(m["mes"].replace("SET", "SET") if m["mes"] != "SEPTIEMBRE" else "SETIEMBRE") + 1
        elif m["rel"]:
            f = ref + timedelta(days={"HOY": 0, "MANANA": 1}.get(m["rel"], 2))
            if m["dr"] and int(m["dr"]) != f.day:   # «MAÑANA 09» debe coincidir con el día de mañana
                return None
            return f
        else:
            k = _DIAS_SEM.index(m["sem"]); f = ref + timedelta(days=(k - ref.weekday()) % 7)
            if m["ds"]:
                d = int(m["ds"]); f2 = date(f.year, f.month, d) if d >= f.day or True else f
                return f2 if f2.weekday() == k else None
            return f
        if not (1 <= mes <= 12 and 1 <= d <= 31):
            return None
        anio = ref.year + (1 if mes < ref.month - 6 else 0)   # «05/01» escrito en diciembre es del año siguiente
        return date(anio, mes, d)
    except (ValueError, IndexError):
        return None


def agenda_de(texto: str, ref: date) -> tuple[date, str] | None:
    """Día y franja que trae el comentario (la última fecha que menciona y la franja u hora que la acompaña).
    ref = día en que se escribió el comentario (para HOY, MAÑANA, LUNES…). None si no trae día Y franja válidos."""
    t = _norm(RE_SELLO.sub(" ", re.sub(r"%[^%]*%", " ", texto or "")))   # sin el sello #dd/mm/aaaa hh:mm# ni el %TÉCNICO%
    for m in reversed(list(_RE_DIA.finditer(t))):   # la última fecha que trae su franja u hora a continuación
        f = _dia(m, ref)
        fr = f and _franja_en(t[:m.end() + 60], m.end())
        if fr:
            return f, fr
    return None


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


def buscar_ab(page: Page, base: str, c: dict) -> list[dict]:
    """Deja en la grilla las OS REGISTRADAS del AB y devuelve [{os, fec, hor, chk}] (chk = id de su casilla)."""
    page.goto(f"{base}{PANTALLA}", wait_until="networkidle")
    _elegir_filial(page, c)
    hace_un_anio = (date.today() - timedelta(days=365)).strftime("%d/%m/%Y")
    page.evaluate(
        """([ab, ini]) => { const v = (id, x) => { const e = document.getElementById(id); if (e) e.value = x; };
            v('MainContent_cboEstadoOS', '001'); v('MainContent_txtNumeroOS', ''); v('MainContent_txtAbonado', ab);
            v('MainContent_txtFechaRegistroIni', ini); }""", [str(int(c["ab"])), hace_un_anio])
    page.click("#MainContent_btnBuscar")
    page.wait_for_timeout(1500)
    try:
        page.wait_for_function(
            r"""(g) => { const t = document.querySelector(g); return (t && t.querySelector('input[id*=chkSeleccion]')) || /TOTAL DE REGISTROS:?\s*0\b/.test(document.body.innerText); }""",
            arg=GRILLA, timeout=60_000)
    except Exception:  # noqa: BLE001
        pass
    return page.evaluate(
        r"""(g) => { const t = document.querySelector(g); if (!t) return [];
            const h = [...t.querySelectorAll('tr')[0].cells].map(c => c.innerText.replace(/\s+/g, ' ').trim().toUpperCase());
            const i = (re) => h.findIndex(x => re.test(x)), iOs = i(/^NRO ?OS$/), iF = i(/FEC\. ?PROG/), iH = i(/HORARIO/), iE = i(/^EST\. ?OS$/), iT = i(/TEC\. ?ASIG/);
            return [...t.querySelectorAll('tr')].filter(tr => tr.querySelector('input[id*=chkSeleccion]')).map(tr => ({
              os: tr.cells[iOs]?.innerText.trim(), fec: tr.cells[iF]?.innerText.trim(), hor: tr.cells[iH]?.innerText.replace(/\s+/g, ' ').trim(),
              est: tr.cells[iE]?.innerText.trim(), tec: tr.cells[iT]?.innerText.replace(/\s+/g, ' ').trim(), chk: tr.querySelector('input[id*=chkSeleccion]').id })); }""", GRILLA)


def _elegir_filial(page: Page, c: dict) -> None:
    page.wait_for_selector("#MainContent_txtNumeroOS", timeout=60_000)
    valor = page.evaluate(
        """(t) => { const n = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase().trim();
                    const o = [...document.querySelector('#MainContent_cboFilial').options].find(o => n(o.text) === t);
                    return o ? o.value : null; }""", _norm(c["filial"]))
    if not valor:
        raise ErrorRobot(f"La filial {c['filial']} no está en ScordSoft.")
    if page.input_value("#MainContent_cboFilial") != valor:
        page.select_option("#MainContent_cboFilial", valor)
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_selector("#MainContent_txtNumeroOS", timeout=60_000)


def agendar(page: Page, base: str, c: dict, fecha: date, franja: str, probar: bool) -> tuple[str, str]:
    """Pone fecha y franja a todas las OS registradas del AB. Devuelve (estado, detalle):
    agendado · igual · omitido (con motivo) · prueba. Lanza ErrorRobot si algo falla."""
    if not c.get("ab"):
        return "omitido", "el comentario no tiene AB"
    f_txt = fecha.strftime("%d/%m/%Y")
    filas = buscar_ab(page, base, c)
    mia = next((f for f in filas if f["os"] == str(c["os"])), None)
    if not mia:
        return "omitido", f"la OS {c['os']} ya no está registrada"
    if all(f["fec"] == f_txt and f["hor"] == franja for f in filas):
        return "igual", f"ya tenía {f_txt} {franja}"
    tec_os = mia.get("tec") or ""
    if not tec_os or re.search(r"S/?\s*ASIGNAR|SIN ASIGNAR", tec_os, re.I):
        return "omitido", "la OS no tiene técnico asignado"
    for f in filas:   # todas las OS registradas del AB (Internet, OTT…)
        page.check(f"#{f['chk']}")
    # La ventana abre con la fecha de hoy, técnico «Seleccione» y horario «NO DEFINE»: se llenan los tres.
    _postback(page, lambda: page.click("#MainContent_tabForm_tpaReportados_btnProgramar"), "#MainContent_pnlProgramacionOS")
    page.wait_for_timeout(1000)
    # TÉCNICO: el que ya tiene la OS (en la lista sale con menos nombres: «MALDONADO SEBASTIAN, RENZO»)
    valor = page.evaluate(
        r"""(t) => { const n = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase().replace(/\s+/g, ' ').trim();
            const T = n(t), ops = [...document.getElementById('MainContent_cboRegidtecnicoProgramacion').options].filter(o => o.index > 0);
            const m = ops.filter(o => { const x = n(o.text); return x === T || T.startsWith(x) || x.startsWith(T); });
            return m.length === 1 ? m[0].value : null; }""", tec_os)
    if not valor:
        page.click("#divCerrarProgramacionOS")
        return "omitido", f"no se encontró al técnico {tec_os} en la lista de ScordSoft"

    def poner_tecnico() -> None:   # elegirlo dispara un postback parcial que vuelve a poner la fecha de hoy
        if page.input_value("#MainContent_cboRegidtecnicoProgramacion") != valor:
            page.select_option("#MainContent_cboRegidtecnicoProgramacion", valor)
            page.wait_for_timeout(1500)
            page.wait_for_load_state("networkidle", timeout=60_000)

    def poner_fecha() -> None:     # cambiarla dispara la revisión de capacidad (otro postback parcial)
        if page.input_value("#MainContent_txtFechaAtencionProgramacion") != f_txt:
            page.fill("#MainContent_txtFechaAtencionProgramacion", f_txt)
            page.evaluate("() => $('#MainContent_txtFechaAtencionProgramacion').trigger('change')")
            page.wait_for_timeout(1500)
            page.wait_for_load_state("networkidle", timeout=60_000)

    # Primero el técnico y después la fecha (el postback del técnico reponía la fecha de hoy); se repasa hasta 3 veces
    for _ in range(3):
        poner_tecnico()
        poner_fecha()
        if (page.input_value("#MainContent_cboRegidtecnicoProgramacion") == valor
                and page.input_value("#MainContent_txtFechaAtencionProgramacion") == f_txt):
            break
    # 3) HORARIO y OBSERVACIÓN
    page.select_option("#MainContent_cboHorarioAtencionOS", label=franja)
    obs = f"Agenda web {f_txt} {franja}: {cuerpo(c)} - {c.get('nombre_usuario') or c.get('usuario') or ''}"[:MAX_TEXTO]
    page.fill("#MainContent_txtObservacionAsignacion", obs)
    revisa = page.evaluate("() => { const g = id => document.getElementById(id); const s = g('MainContent_cboRegidtecnicoProgramacion'), h = g('MainContent_cboHorarioAtencionOS');"
                           " return [g('MainContent_txtFechaAtencionProgramacion').value, s.value, h.options[h.selectedIndex].text, s.options[s.selectedIndex].text]; }")
    if revisa[:3] != [f_txt, valor, franja]:
        page.click("#divCerrarProgramacionOS")
        raise ErrorRobot(f"La ventana no quedó con la fecha, técnico y franja pedidos ({revisa}); no se grabó.")
    tec = revisa[3]
    if probar:
        _captura(page, CAPTURAS, f"prueba_agenda_{c['os']}")
        page.click("#divCerrarProgramacionOS")
        return "prueba", f"{f_txt} {franja} · técnico {tec} · {len(filas)} OS"
    avisos: list[str] = []
    page.once("dialog", lambda d: (avisos.append(d.message), d.accept()))
    _postback(page, lambda: page.click("#MainContent_btnGrabarProgramacion"))
    page.wait_for_timeout(1000)
    msg = (page.inner_text("#MainContent_lblMsgEdicionProgramacionOS") if page.locator("#MainContent_lblMsgEdicionProgramacionOS").count() else "").strip()
    if msg or avisos:
        print(f"   ScordSoft dijo: {msg} {' '.join(avisos)}".rstrip(), flush=True)
    filas = buscar_ab(page, base, c)
    mia = next((f for f in filas if f["os"] == str(c["os"])), None)
    if mia and mia["fec"] == f_txt and mia["hor"] == franja:
        return "agendado", f"{f_txt} {franja} · técnico {tec} · {len(filas)} OS"
    raise ErrorRobot("GRABADO_SIN_CONFIRMAR: se pulsó Grabar en Agendar Orden pero la OS no muestra la fecha y franja. "
                     + (msg or " ".join(avisos)))


def procesar_agenda(page: Page, base: str, c: dict, probar: bool) -> None:
    """Después de escribir el comentario: si trae día y franja, agenda la OS en ScordSoft (y lo deja anotado en Supabase)."""
    ag = agenda_de(c["texto"], _dia_lima(c["creado_en"]))
    if not ag or c.get("ag_estado"):
        return
    fecha, franja = ag
    base_campos = {"ag_fecha": fecha.isoformat(), "ag_franja": franja, "ag_en": _ahora()}
    hoy = datetime.now(timezone(timedelta(hours=-5))).date()
    try:
        if hay_agenda_mas_nueva(c):
            est, det = "reemplazado", "hay un comentario más nuevo con agenda"
        elif fecha < hoy:
            est, det = "omitido", f"la fecha {fecha:%d/%m} ya pasó"
        else:
            est, det = agendar(page, base, c, fecha, franja, probar)
    except Exception as e:  # noqa: BLE001
        _captura(page, CAPTURAS, f"error_agenda_{c['os']}_{int(time.time())}")
        est, det = "error", str(e)[:300]
    print(f"   agenda {fecha:%d/%m} {franja} → {est} ({det})", flush=True)
    if not probar:
        marcar(c["id"], ag_estado=est, ag_error=None if est in ("agendado", "igual") else det, **base_campos)


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
                if r in ("escrito", "ya_estaba", "prueba"):
                    procesar_agenda(page, base, c, probar)
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
