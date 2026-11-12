"""Punto de entrada del robot.

  python -m sync.main                         # una corrida: descarga el periodo, calcula y guarda lo que cambió
  python -m sync.main --excel archivo.xlsx    # usa un Excel ya descargado (sin robot)
  python -m sync.main --excel a.xlsx --sin-subir   # solo procesa y calcula, no escribe en Supabase
  python -m sync.main --solo-descargar r.xlsx      # solo descarga el reporte (el turno descarga y procesa en paralelo)
  python -m sync.main --excel r.xlsx --corte ISO   # procesa un reporte ya descargado, con la hora en que se pidió
  python -m sync.main --solo-descargar r.xlsx --solo-mes-actual   # descarga RÁPIDA: solo el mes en curso
  python -m sync.main --excel rapido.xlsx --base completo.xlsx    # junta la base completa con la descarga rápida

Cada corrida descarga UN reporte, igual que a mano en ScordSoft:
  Cronograma de Trabajo (Órdenes de Servicio) · Filial SELECCIONE (todas) · por Registro
  · del 1 del mes anterior al último día del mes en curso.

Con ese único archivo se calcula todo en memoria (tableros de Instalaciones y Averías de cada filial, mes en curso y
mes anterior, e indicadores de la portada). Supabase solo recibe lo que cambió; no se relee la base.
"""
from __future__ import annotations

import argparse
import re
import calendar
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import procesar

LIMA = ZoneInfo("America/Lima")
FILIALES = ["ANCASH", "AREQUIPA", "CAJAMARCA", "HUANUCO", "LA LIBERTAD", "LAMBAYEQUE", "LIMA", "PIURA"]
# Suspensiones, reconexiones y cortes que hace OFICINA (sin EECC), ya descargados: son ~80 % de las órdenes y nada del
# portal las lee de la base (Avance total solo cuenta trabajos de EECC y sale del reporte). No se suben a Supabase para
# no llenar la base (límite 500 MB del plan); siguen en el reporte, así que tampoco se borran las que ya estaban.
TIPOS_OFICINA = {"SUSPENSION DE SERVICIO", "RECONEXION SERVICIO", "CORTE DEFINITIVO"}
_RE_MES_CERRADO = re.compile(r":\d{4}-\d{2}$")   # tableros del mes anterior (instalaciones:LIMA:2026-10, …)


def _solo_oficina(f: dict) -> bool:
    return (f.get("tipo_orden") in TIPOS_OFICINA and f.get("estado_os") == "DESCARGADA"
            and not str(f.get("contrata") or "").strip().upper().startswith("EECC"))


TIMESTAMPS = ("fecha_registro", "atencion_inicio", "atencion_fin", "fecha_observacion", "fecha_mod", "inicio_fachada")
ESTADO = Path(os.environ.get("ESTADO_ROBOT", ".estado/robot.json.gz"))


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def periodo(hoy: date) -> tuple[date, date]:
    """Del 1 del mes anterior al último día del mes en curso (p. ej. 01/09/2026 – 31/10/2026)."""
    primero = hoy.replace(day=1)
    desde = (primero - timedelta(days=1)).replace(day=1)
    hasta = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
    return desde, hasta


def _fin_mes_anterior(hoy: date) -> datetime:
    """Cierre del mes anterior (tablero del mes anterior que usa el selector de mes del portal)."""
    return datetime.combine(hoy.replace(day=1) - timedelta(days=1), datetime.min.time()).replace(hour=23, minute=59)


def _tiempos(corte: str | None, t_ini: float, t_proc: float, t_tab: float) -> str:
    """Cuánto tomó cada parte de la actualización (queda en el registro de sincronizaciones para medir y mejorar)."""
    try:
        espera = t_ini - datetime.fromisoformat(corte).timestamp() if corte else 0
    except Exception:  # noqa: BLE001
        espera = 0
    ahora = time.time()
    return (f"t: descarga+cola {espera:.0f}s, lectura {t_proc - t_ini:.0f}s, tableros {t_tab - t_proc:.0f}s, "
            f"guardado {ahora - t_tab:.0f}s")


def _filas_base(archivo: Path, df0: pd.DataFrame, inicio: str, cajas) -> list[dict]:
    """Filas ya limpias de un reporte de la BASE (la última completa), guardadas al lado: la base se reutiliza en cada
    rápida y limpiarla de nuevo tomaba ~1 min. Se rehace si cambió el índice de cajas."""
    import pickle
    firma = getattr(cajas, "n", None)
    ruta = archivo.with_name(archivo.name + ".filas.pkl")
    try:
        if ruta.exists():
            with open(ruta, "rb") as f:
                guardado = pickle.load(f)
            if guardado.get("firma") == firma:
                filas = guardado["filas"]
                for x in filas:
                    x["sincronizado_en"] = inicio
                return filas
    except Exception:  # noqa: BLE001 - copia dañada: se vuelve a limpiar
        pass
    filas = procesar.a_filas(df0, inicio, cajas)
    try:
        with open(ruta, "wb") as f:
            pickle.dump({"firma": firma, "filas": filas}, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # noqa: BLE001
        pass
    return filas


def _cada_hora(nombre: str, funcion, *a, horas: float = 4):
    """Resultado guardado en disco (junto al estado del robot) y reutilizado por unas horas: los mapas de cajas y de
    áreas casi no cambian y bajarlos tomaba tiempo. Si la descarga falla (o devuelve None) se usa la ÚLTIMA COPIA
    BUENA aunque sea vieja: así las cajas no cambian y la base ya limpia se reutiliza (antes se volvía a limpiar
    toda, ~2 min más). Solo sin ninguna copia se devuelve None (o se propaga el error)."""
    import pickle
    ruta = ESTADO.parent / f"cache_{nombre}.pkl"

    def copia():
        with open(ruta, "rb") as f:
            return pickle.load(f)
    try:
        if ruta.exists() and time.time() - ruta.stat().st_mtime < horas * 3600:
            return copia()
    except Exception:  # noqa: BLE001 - copia dañada: se vuelve a calcular
        pass
    try:
        r = funcion(*a)
    except Exception as e:  # noqa: BLE001
        if ruta.exists():
            print(f"Aviso: {nombre}: no se pudo actualizar ({str(e)[:120]}); se usa la última copia buena.", flush=True)
            return copia()
        raise
    if r is None:
        if ruta.exists():
            print(f"Aviso: {nombre}: la descarga vino incompleta; se usa la última copia buena.", flush=True)
            try:
                return copia()
            except Exception:  # noqa: BLE001
                return None
        return None
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "wb") as f:
            pickle.dump(r, f)
    except Exception:  # noqa: BLE001
        pass
    return r


def _cols(df: pd.DataFrame, columnas: str, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    """Subconjunto con las columnas que antes se pedían a la base (las que falten se agregan vacías)."""
    nombres = [*extra, *columnas.split(",")]
    x = df.reindex(columns=nombres)
    return x


def _leer_con_cache(archivo: Path) -> pd.DataFrame:
    """Lee un reporte de la base guardando una copia ya leída al lado: la base se reutiliza en varias
    actualizaciones rápidas y así el Excel grande se lee una sola vez."""
    cache = archivo.with_name(archivo.name + ".pkl")
    if cache.exists():
        try:
            return pd.read_pickle(cache)
        except Exception:  # noqa: BLE001 - copia dañada: se vuelve a leer el Excel
            pass
    df0 = procesar.leer_excel(archivo)
    try:
        df0.to_pickle(cache)
    except Exception:  # noqa: BLE001
        pass
    return df0


DIAS_BOLSA = 7   # días de fotos de la bolsa inicial que se guardan (los anteriores se estiman en el portal)


def _bolsa_inicial(tableros: dict[str, dict], estado: dict, ahora: datetime) -> None:
    """Foto de la bolsa de pendientes (altas REGISTRADA) al iniciar el día, una por filial y por día.

    Se toma en la actualización de las 0:00 (o, si esa falló, en la primera antes de las 9:00) y ya no cambia en el
    día: el portal saca de ella el objetivo del día (80 % de la bolsa inicial). Queda en el tablero de Instalaciones
    («bini»: {fecha: {c: hora de la foto, f: filas [os, eecc, técnico, segmento, canal, zona, filial, F. AG, tipo AG/CO/SA]}}) y en el
    estado del robot, para no perderla en las siguientes corridas."""
    hoy = ahora.date().isoformat()
    B = estado.setdefault("bini", {})
    if not B:   # estado nuevo (caché de GitHub vacía): se recuperan las fotos ya guardadas en Supabase
        from . import subir

        r = subir._pedir("GET", subir._url("tableros") + "?select=clave,bini:datos->bini,st:datos->_st&clave=like.instalaciones:*",
                         "Bolsa inicial", headers=subir._cabeceras())
        for x in r.json():
            partes = x["clave"].split(":")
            if len(partes) != 2:
                continue
            bini = x.get("bini")
            if x.get("st"):   # tablero guardado en Storage: se baja el archivo para sacar la bolsa
                try:
                    bini = (subir.leer_tablero(x["clave"]) or {}).get("bini")
                except Exception as e:  # noqa: BLE001
                    print(f"Aviso: no se leyó la bolsa inicial de {x['clave']}: {e}", flush=True)
            if isinstance(bini, dict):
                B[partes[1]] = bini
    for clave, datos in tableros.items():
        partes = clave.split(":")
        if partes[0] != "instalaciones" or len(partes) != 2:
            continue
        dias = B.setdefault(partes[1], {})
        if hoy not in dias and ahora.hour < 9:
            dias[hoy] = {"c": ahora.strftime("%H:%M"),
                         "f": [[x.get("os"), x.get("ec"), x.get("tec"), x.get("seg"), x.get("cn"), x.get("z"), x.get("fi"), x.get("fp"), x.get("t")]
                               for x in datos.get("pend", [])]}
            print(f"  Bolsa inicial del {hoy} de {partes[1]}: {len(dias[hoy]['f'])} pendientes (foto de las {dias[hoy]['c']}).", flush=True)
        for viejo in sorted(dias)[:-DIAS_BOLSA]:
            del dias[viejo]
        datos["bini"] = dias


_RE_ANOT = re.compile(r"#\s*\d{1,2}/\d{1,2}/\d{4}\s*\d{1,2}:\d{2}\s*#")


def _ultima_anotacion(t) -> str:
    """Última anotación del comentario del técnico (desde su #dd/mm/aaaa hh:mm#), sin teléfonos."""
    t = str(t or "").strip()
    if not t:
        return ""
    ms = list(_RE_ANOT.finditer(t))
    from .procesar import _sin_tel

    return _sin_tel(t[ms[-1].start():] if ms else t[-300:])[:300]


def _cambios_agenda(tableros: dict[str, dict], estado: dict, ahora: datetime, asignacion: dict[str, list]) -> None:
    """Cambios de agenda del día de los pendientes (instalaciones y misceláneos REGISTRADA), por filial.

    En cada actualización se compara la fecha y franja (AGENDA o COORDINADA) de cada OS con la de la actualización
    anterior (guardada en el estado del robot, sin leer Supabase). Si la OS tenía agenda y cambió de fecha, de franja
    o se quedó sin ella, se anota: [hora actualización anterior, hora de esta, fecha y franja antes, fecha y franja
    ahora, observación de la asignación en ScordSoft, usuario, última anotación del técnico]. Va al tablero de
    Instalaciones como «camb» {"FILIAL|OS": [cambios del día]} y se reinicia cada día."""
    hoy, hora = ahora.date().isoformat(), ahora.strftime("%H:%M")
    A = estado.setdefault("agd", {})
    for clave, datos in tableros.items():
        partes = clave.split(":")
        if partes[0] != "instalaciones" or len(partes) != 2:
            continue
        fil = partes[1]
        st = A.get(fil) or {}
        prev, h0 = st.get("v") or {}, st.get("h") or ""
        camb = (st.get("c") or {}) if st.get("dia") == hoy else {}
        cur, nuevos = {}, 0
        for x in (datos.get("pend") or []) + (datos.get("pendx") or []):
            if x.get("os") is None:
                continue
            k = f"{(x.get('fi') or fil).upper()}|{x['os']}"
            ag = [x.get("fp") or "", " ".join(str(x.get("fr") or "").split())] if x.get("t") in ("AG", "CO") and x.get("fp") else ["", ""]
            cur[k] = ag
            p = prev.get(k)
            if p and p[0] and p != ag:   # tenía agenda y cambió (fecha, franja o se quedó sin agenda)
                a = asignacion.get(k) or ["", ""]
                camb.setdefault(k, []).append([h0, hora, p[0], p[1], ag[0], ag[1], a[0], a[1], _ultima_anotacion(x.get("ovt"))])
                nuevos += 1
        if nuevos:
            print(f"  Cambios de agenda en {fil}: {nuevos} OS cambiaron de fecha o franja ({len(camb)} en el día).", flush=True)
        A[fil] = {"dia": hoy, "h": hora, "v": cur, "c": camb}
        datos["camb"] = camb


def _tab_filial(filial: str) -> dict[str, dict]:
    """Tableros de UNA filial (o del bloque PROVINCIAS). Se corre en procesos aparte (uno por núcleo) y lee lo común
    de _CTX, que los procesos heredan al crearse (fork), así no se copian las órdenes en cada llamada."""
    from . import averias, instalaciones, resumen
    c = _CTX
    d, desde, fa, tecnicos, fin_ant, corte, zonas_prov, hoy, actualizado = (
        c["grupos"][filial], c["desde"], c["fa"], c["tecnicos"], c["fin_ant"], c["corte"], c["zonas_prov"], c["hoy"], c["actualizado"])
    tableros: dict[str, dict] = {}
    t0 = time.time()
    # ---------------- indicadores de la portada / Dashboard
    tableros[f"indicadores:{filial}"] = {**resumen.indicadores(d, actualizado), "filial": filial}

    # ---------------- Averías y TC
    zz = averias.ZONAS if filial == "LIMA" else zonas_prov if filial == "PROVINCIAS" else {}
    av = _cols(d[d["tipo_orden"].isin(["SERVICIO TECNICO", *averias.TIPOS_ORDEN_MISC])], averias.COLUMNAS_BD)
    ins = _cols(d[(d["tipo_orden"] == "INSTALACION SERVICIO") & (d["estado_os"] == "DESCARGADA")
                  & (fa[d.index] >= pd.Timestamp(desde - timedelta(days=31)))], averias.COLUMNAS_INST, ("id_os", "filial"))
    otros_av = _cols(d[(d["estado_os"] == "REGISTRADA") & (d["tipo_orden"] != "INSTALACION SERVICIO")],
                     instalaciones.COLUMNAS_BD, ("tipo_orden", "detalle"))
    if not av.empty:
        try:
            ant = averias.construir(av, tecnicos, fin_ant, zonas=zz, inst=ins)
            ant["filial"] = filial
            tableros[f"averias:{filial}:{fin_ant:%Y-%m}"] = ant
        except Exception as e:  # noqa: BLE001 - el mes en curso se guarda igual
            print(f"Aviso: no se calculó el mes anterior de averías {filial}: {e}", flush=True)
        datos = averias.construir(av, tecnicos, corte, zonas=zz, inst=ins)
        datos["pendx"] = instalaciones.pendientes_otros_averias(otros_av, tecnicos, corte, filial=filial,
                                                                zonas=instalaciones.ZONAS if filial == "LIMA" else {})
        datos["filial"] = filial
        tableros[f"averias:{filial}"] = datos

    # ---------------- Instalaciones
    it = _cols(d[d["tipo_orden"] == "INSTALACION SERVICIO"], instalaciones.COLUMNAS_BD)
    if not it.empty:
        zz = instalaciones.ZONAS if filial == "LIMA" else zonas_prov if filial == "PROVINCIAS" else {}
        try:
            ant = instalaciones.construir(it, tecnicos, fin_ant, filial=filial, zonas=zz)
            ant["filial"] = filial
            ant.pop("obs", None)
            tableros[f"instalaciones:{filial}:{fin_ant:%Y-%m}"] = ant
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se calculó el mes anterior de instalaciones {filial}: {e}", flush=True)
        otros_in = _cols(d[(d["estado_os"] == "REGISTRADA") & (d["tipo_orden"] != "INSTALACION SERVICIO")],
                         instalaciones.COLUMNAS_BD, ("tipo_orden", "detalle"))
        avt = _cols(d[(d["estado_os"] == "DESCARGADA") & d["contrata"].astype(str).str.startswith("EECC")
                      & (fa[d.index] >= pd.Timestamp(hoy.replace(day=1)))], instalaciones.COLUMNAS_AVT)
        datos = instalaciones.construir(it, tecnicos, corte, filial=filial, zonas=zz)
        datos["pendx"] = instalaciones.pendientes_otros(otros_in, tecnicos, corte, filial=filial, zonas=zz, excluir=("INSTALACION SERVICIO",),
                                                          permitidos=instalaciones.DET_INSTALACIONES - {"INSTALACION"})
        datos["avt"] = instalaciones.avance_total(avt, datos["anio"], datos["mes"])
        sem = _cols(d[(d["estado_os"] == "DESCARGADA") & d["contrata"].astype(str).str.startswith("EECC")
                      & (fa[d.index] >= pd.Timestamp(hoy - timedelta(days=7)))], instalaciones.COLUMNAS_AVT)
        datos["tec7"] = instalaciones.tecnicos_semana(sem)
        datos["filial"] = filial
        tableros[f"observadas:{filial}"] = {"obs": datos.pop("obs"), "filial": filial, "corte": datos["corte"]}
        tableros[f"instalaciones:{filial}"] = datos
    print(f"  Tableros de {filial} calculados en {time.time() - t0:.0f} s.", flush=True)
    return tableros


_CTX: dict = {}


def calcular_tableros(df: pd.DataFrame, ahora: datetime, actualizado: str, links_st: dict | None = None) -> dict[str, dict]:
    """Todos los tableros del portal a partir de las órdenes del periodo (sin leer Supabase)."""
    from . import averias, instalaciones, resumen

    hoy = ahora.date()
    corte = ahora.replace(tzinfo=None)
    fin_ant = _fin_mes_anterior(hoy)
    desde = averias.inicio_periodo(hoy)
    df = df.copy()
    # Mismo formato que devolvía Supabase (timestamptz en UTC: "2026-10-20T03:00:00+00:00"), para que los tableros
    # salgan idénticos a cuando se calculaban leyendo la base.
    for c in TIMESTAMPS:
        if c in df:
            t = pd.to_datetime(df[c], utc=True, errors="coerce")
            df[c] = t.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00").where(t.notna(), None)
    reg = pd.to_datetime(df["fecha_registro"], utc=True, errors="coerce").dt.tz_convert(LIMA)
    df = df[reg >= pd.Timestamp(desde).tz_localize(LIMA)]
    tecnicos = resumen.tecnico_contrata(df, pd.Timestamp(corte).tz_localize(LIMA))
    fa = pd.to_datetime(df["fecha_atencion"], errors="coerce")
    tableros: dict[str, dict] = {}

    fil = df["filial"].fillna("SIN FILIAL")
    grupos = [(f, df[fil == f]) for f in sorted(fil.unique())]
    # Bloque PROVINCIAS: todas las filiales menos Lima juntas (el portal elige Lima o Provincias y luego, si quiere, una filial).
    prov = df[~fil.isin(["LIMA", "SIN FILIAL"])]
    if not prov.empty:
        grupos.append(("PROVINCIAS", prov))
    # En PROVINCIAS la «zona» de cada distrito es su filial: los cuadros por zona se leen por filial.
    zonas_prov = {}
    if not prov.empty:
        dd = prov.assign(_d=prov["distrito"].fillna("").astype(str).str.upper().str.strip(), _f=prov["filial"].astype(str))
        zonas_prov = {d: g.mode().iat[0] for d, g in dd[dd["_d"] != ""].groupby("_d")["_f"]}
    # Cada filial (y PROVINCIAS) se calcula en su propio proceso, a la vez (antes, una tras otra: ~95 s).
    global _CTX
    _CTX = {"grupos": dict(grupos), "desde": desde, "fa": fa, "tecnicos": tecnicos, "fin_ant": fin_ant, "corte": corte,
            "zonas_prov": zonas_prov, "hoy": hoy, "actualizado": actualizado}
    orden = sorted(_CTX["grupos"], key=lambda f: -len(_CTX["grupos"][f]))   # las más grandes primero
    hechas: dict[str, dict] = {}
    try:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 2)), mp_context=multiprocessing.get_context("fork")) as ex:
            for filial, parte in zip(orden, ex.map(_tab_filial, orden)):
                hechas[filial] = parte
    except Exception as e:  # noqa: BLE001 - si el paralelo falla, se calcula como antes (una tras otra)
        print(f"Aviso: tableros en paralelo no disponibles ({str(e)[:150]}); se calculan uno tras otro.", flush=True)
        for filial in orden:
            if filial not in hechas:
                hechas[filial] = _tab_filial(filial)
    for filial, _ in grupos:   # mismo orden de siempre
        tableros.update(hechas[filial])
    _CTX = {}
    try:  # Buscador FFTT: un solo tablero con todas las filiales (la página filtra y valida)
        from . import fftt
        tableros["fftt"] = fftt.construir(df, actualizado)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se calculó el tablero FFTT: {e}", flush=True)
    try:  # Speed Test: un solo tablero con todas las filiales (la página filtra)
        from . import speedtest
        tableros["speedtest"] = speedtest.construir(df, links_st or {}, tecnicos, actualizado)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se calculó el tablero de Speed Test: {e}", flush=True)
    try:  # Áreas de los planos (Google My Maps) para el mapa de Ruteo de Pendientes
        from . import areas
        ar = _cada_hora("areas", areas.cargar, actualizado)
        if ar is not None:
            tableros["areas"] = ar
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se calcularon las áreas de los planos: {e}", flush=True)
    try:  # Zonificación: todos los planos de cada distrito (catálogo de Lima + planos vistos en las órdenes)
        from . import zonificacion
        tableros["zonificacion"] = zonificacion.actualizar(df, actualizado, tableros)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se calculó el tablero de Zonificación: {e}", flush=True)
    return tableros


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, nargs="*", help="procesar Excel locales en vez de usar el robot")
    ap.add_argument("--sin-subir", action="store_true", help="solo procesar y calcular, no escribir en Supabase")
    ap.add_argument("--reescribir-todo", action="store_true", help="reescribir todas las órdenes (ignora las huellas)")
    ap.add_argument("--solo-descargar", type=Path, help="solo descargar el reporte del periodo a esta ruta y terminar")
    ap.add_argument("--corte", help="hora (ISO, UTC) en que se pidió el reporte: se usa como corte y como «actualizado»")
    ap.add_argument("--solo-mes-actual", action="store_true",
                    help="con --solo-descargar: descarga RÁPIDA (mes en curso + lo ejecutado hoy)")
    ap.add_argument("--base", type=Path, nargs="*", default=[],
                    help="reporte(s) completo(s) anteriores: se leen primero y las órdenes de --excel los reemplazan")
    ap.add_argument("--parche", type=Path, nargs="*", default=[],
                    help="reportes EXPRÉS (hoy por ejecución + registro de 2 días): se aplican al final; de los que no "
                         "están en la base solo entran los registrados dentro del periodo")
    ap.add_argument("--solo-descargar-expres", type=Path,
                    help="solo descargar los reportes EXPRÉS (chicos, ~2 min) a esta carpeta y terminar")
    args = ap.parse_args(argv)

    if args.solo_descargar_expres:
        # Robot EXPRÉS: entre las actualizaciones normales trae lo que cambió hoy (lo liquidado hoy por fecha de
        # ejecución y lo registrado en los últimos 2 días). Si falla, el robot principal sigue igual.
        from .descargar import descargar_reportes

        hoy = datetime.now(LIMA).date()
        d = args.solo_descargar_expres
        d.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            descargar_reportes([(d / "ejec.xlsx", hoy, hoy, "TODAS", "ejecucion"),
                                (d / "reg.xlsx", hoy - timedelta(days=2), hoy, "TODAS", "registro")], espera_max_s=300)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR en la descarga exprés: {e}", file=sys.stderr, flush=True)
            return 1
        print(f"[tiempo] descarga exprés: {time.time() - t0:.0f} s", flush=True)
        return 0

    if args.solo_descargar:
        # Turno en paralelo: la descarga (lo más lento, ~14 min) corre mientras se procesa el reporte anterior.
        from .descargar import descargar_reportes

        hoy = datetime.now(LIMA).date()
        d0, d1 = periodo(hoy)
        t0 = time.time()
        try:
            # Se descarga en una carpeta aparte: así el procesador nunca ve archivos a medio bajar.
            carpeta = Path(tempfile.mkdtemp(prefix="scordsoft_"))
            if args.solo_mes_actual:
                # RÁPIDA: el mes en curso (todas las filiales juntas) + lo EJECUTADO HOY (cualquier fecha de registro,
                # así lo liquidado hoy de un AB del mes anterior también se ve). El mes anterior completo viene de la
                # última descarga completa: la data siempre tiene los 2 meses.
                d0 = hoy.replace(day=1)
                print(f"Descarga RÁPIDA: mes en curso {d0:%d/%m/%Y}–{d1:%d/%m/%Y} + lo ejecutado hoy", flush=True)
                pedidos = [(carpeta / "reporte.xlsx", d0, d1, "TODAS", "registro"),
                           (carpeta / "ejec.xlsx", hoy, hoy, "TODAS", "ejecucion")]
            else:
                # COMPLETA (mes anterior + mes en curso) por filial, LIMA primero: ScordSoft arma rápido los reportes
                # chicos y una filial lenta no frena a las demás (sin esa filial no se borra nada).
                orden = ["LIMA"] + [f for f in FILIALES if f != "LIMA"]
                pedidos = [(carpeta / ("reporte.xlsx" if i == 0 else f"f{i}.xlsx"), d0, d1, f, "registro")
                           for i, f in enumerate(orden)]
            # Si «todas las filiales» llega con una sola, las demás se piden aparte (archivos <nombre>_N.xlsx).
            r = descargar_reportes(pedidos, espera_max_s=900)
            ejec = [x for x in r if x.name.startswith("ejec")]
            prin = [x for x in r if not x.name.startswith("ejec")]
            if not prin:
                raise RuntimeError("No llegó el reporte principal.")
            partes = args.solo_descargar.parent / "partes"
            marca = args.solo_descargar.name.split(".")[0]
            for i, extra in enumerate(prin[1:]):
                partes.mkdir(exist_ok=True)
                shutil.move(str(extra), str(partes / f"{marca}_{i}.xlsx"))
            for i, e in enumerate(ejec):   # lo ejecutado hoy va aparte: se aplica encima como parche
                shutil.move(str(e), str(args.solo_descargar.parent / f"{marca}.ejec{i}.xlsx"))
            shutil.move(str(prin[0]), str(args.solo_descargar))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR en la descarga: {e}", file=sys.stderr, flush=True)
            return 1
        print(f"[tiempo] descarga: {time.time() - t0:.0f} s", flush=True)
        return 0

    inicio = args.corte or _ahora()
    t_ini = time.time()
    ahora = datetime.fromisoformat(args.corte).astimezone(LIMA) if args.corte else datetime.now(LIMA)
    desde, hasta = periodo(ahora.date())
    total = 0
    try:
        # ---------------- 1. Descarga (un solo reporte)
        if args.excel or args.base or args.parche:
            # Primero la base completa (mes anterior + mes en curso de hace un rato) y después el reporte nuevo:
            # cada orden queda con su versión más reciente.
            archivos = [*args.base, *(args.excel or [])]
        else:
            from .descargar import descargar_reportes

            carpeta = Path(tempfile.mkdtemp(prefix="scordsoft_"))
            archivos = descargar_reportes([(carpeta / "reporte.xlsx", desde, hasta, "TODAS", "registro")])
        t_desc = time.time()
        print(f"[tiempo] descarga: {t_desc - t_ini:.0f} s", flush=True)

        # ---------------- 2. Limpieza del Excel
        indice_cajas = None
        try:
            from . import cajas

            indice_cajas = _cada_hora("cajas", cajas.cargar)
            if indice_cajas:
                print(f"Cajas de planta cargadas: {indice_cajas.n}.", flush=True)
        except Exception as e:  # noqa: BLE001 - sin mapas se conservan las cajas ya guardadas
            print(f"Aviso: no se cargaron los mapas de cajas: {e}", flush=True)
        filas_por_id: dict[int, dict] = {}
        links_st: dict[int, list] = {}
        asignacion: dict[str, list] = {}
        base = set(args.base)
        for archivo in archivos:
            df0 = _leer_con_cache(archivo) if archivo in base else procesar.leer_excel(archivo)
            try:
                links_st.update(procesar.speedtest_por_os(df0))
            except Exception as e:  # noqa: BLE001 - Speed Test nunca detiene la corrida
                print(f"Aviso: no se leyeron los enlaces de Speedtest: {e}", flush=True)
            try:
                asignacion.update(procesar.asignacion_por_os(df0))
            except Exception as e:  # noqa: BLE001 - el motivo de los cambios de agenda nunca detiene la corrida
                print(f"Aviso: no se leyó la observación de asignación: {e}", flush=True)
            for fila in _filas_base(archivo, df0, inicio, indice_cajas) if archivo in base else procesar.a_filas(df0, inicio, indice_cajas):
                filas_por_id[fila["id_os"]] = fila
        if args.parche:   # exprés: lo más nuevo pisa a la base; nunca entran órdenes de fuera del periodo
            ini_per = pd.Timestamp(desde).tz_localize(LIMA)
            nuevas = cambiadas = 0
            for archivo in args.parche:
                df0 = procesar.leer_excel(archivo)
                try:
                    links_st.update(procesar.speedtest_por_os(df0))
                    asignacion.update(procesar.asignacion_por_os(df0))
                except Exception as e:  # noqa: BLE001
                    print(f"Aviso: exprés sin enlaces/asignación: {e}", flush=True)
                for fila in procesar.a_filas(df0, inicio, indice_cajas):
                    k = fila["id_os"]
                    if k not in filas_por_id:
                        r = pd.to_datetime(fila.get("fecha_registro"), utc=True, errors="coerce")
                        if pd.isna(r) or r.tz_convert(LIMA) < ini_per:
                            continue
                        nuevas += 1
                    elif filas_por_id[k].get("estado_os") != fila.get("estado_os"):
                        cambiadas += 1
                    filas_por_id[k] = fila
            print(f"Exprés: {nuevas} órdenes nuevas y {cambiadas} que cambiaron de estado respecto de la base.", flush=True)
        filas = list(filas_por_id.values())
        if not filas:
            raise RuntimeError("El reporte llegó vacío.")
        por_filial: dict[str, int] = {}
        for f in filas:
            k = f.get("filial") or "SIN FILIAL"
            por_filial[k] = por_filial.get(k, 0) + 1
        print(f"{len(filas)} órdenes en el reporte: " + ", ".join(f"{k} {v}" for k, v in sorted(por_filial.items())), flush=True)
        t_proc = time.time()
        print(f"[tiempo] limpieza: {t_proc - t_desc:.0f} s", flush=True)

        # ---------------- 3. Tableros e indicadores (en memoria)
        df = pd.DataFrame(filas)
        # PROTECCIÓN: si el reporte llegó sin el mes anterior (p. ej. ScordSoft devolvió solo el mes en curso), no se
        # borra nada de la base ni se tocan los tableros del mes anterior: solo se actualiza el mes en curso.
        ant_incompleto = False
        try:
            reg = pd.to_datetime(df["fecha_registro"], utc=True, errors="coerce").dt.tz_convert(LIMA)
            ini_mes = pd.Timestamp(ahora.date().replace(day=1)).tz_localize(LIMA)
            n_ant = int(((reg >= pd.Timestamp(desde).tz_localize(LIMA)) & (reg < ini_mes)).sum())
            n_act = int((reg >= ini_mes).sum())
            ant_incompleto = n_ant < 0.3 * n_act
            if ant_incompleto:
                print(f"AVISO: el reporte trae solo {n_ant} órdenes del mes anterior frente a {n_act} del mes en curso: "
                      "no se borra nada ni se tocan los tableros del mes anterior.", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se pudo revisar el mes anterior: {e}", flush=True)
        tableros = calcular_tableros(df, ahora, inicio, links_st)
        if ant_incompleto:
            sufijo = f":{_fin_mes_anterior(ahora.date()):%Y-%m}"
            for k in [k for k in tableros if k.endswith(sufijo)]:
                tableros.pop(k)
        t_tab = time.time()
        print(f"[tiempo] tableros: {t_tab - t_proc:.0f} s ({len(tableros)} tableros)", flush=True)

        if args.sin_subir:
            return 0

        # ---------------- 4. Guardar solo lo que cambió
        from . import subir

        estado = None if args.reescribir_todo else subir.cargar_estado(ESTADO)
        if estado is None:
            estado = subir.estado_desde_base()
            if args.reescribir_todo:
                estado["arranque"] = False
                for v in estado["o"].values():
                    v[0] = "forzar"
        try:   # foto de la bolsa de pendientes al iniciar el día (objetivo fijo del Avance de producción)
            _bolsa_inicial(tableros, estado, ahora)
        except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
            print(f"Aviso: no se guardó la bolsa inicial del día: {e}", flush=True)
        # Speed Test: leer las velocidades de speedtest.net toma hasta ~1,5 min; se hace EN PARALELO con la escritura de
        # las órdenes (no se tocan entre sí) y se espera antes de guardar los tableros.
        import threading
        st_hilo = None
        try:
            if "speedtest" in tableros:
                from . import speedtest as _st
                stv = estado.setdefault("stv", {})
                turno = os.environ.get("GITHUB_RUN_ID", "local")
                if estado.get("stv_turno") != turno:   # una vez por turno: se suman las lecturas ya guardadas en Supabase
                    n0 = len(stv)
                    for k, v in (subir.leer_tablero("speedtest_vel").get("v") or {}).items():
                        stv.setdefault(k, v)
                    estado["stv_turno"] = turno
                    print(f"  Speed Test: {len(stv) - n0} velocidades recuperadas de Supabase ({len(stv)} en total).", flush=True)

                def _leer_st():
                    t_st = time.time()
                    try:
                        _st.velocidades(tableros["speedtest"], stv, segundos=90)
                    except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
                        print(f"Aviso: no se leyeron las velocidades de Speed Test: {e}", flush=True)
                    print(f"  [tiempo] Speed Test: {time.time() - t_st:.0f} s (en paralelo con las órdenes)", flush=True)
                st_hilo = threading.Thread(target=_leer_st, daemon=True)
                st_hilo.start()
        except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
            print(f"Aviso: no se leyeron las velocidades de Speed Test: {e}", flush=True)
        try:   # cambios de agenda del día (fecha o franja) respecto de la actualización anterior
            _cambios_agenda(tableros, estado, ahora, asignacion)
        except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
            print(f"Aviso: no se calcularon los cambios de agenda: {e}", flush=True)
        a_subir = [f for f in filas if not _solo_oficina(f)]
        if len(a_subir) < len(filas):
            print(f"{len(filas) - len(a_subir)} suspensiones / reconexiones / cortes de oficina no se suben a la base (no los usa el portal).", flush=True)
        total = subir.upsert_ordenes(a_subir, estado, cajas_ok=indice_cajas is not None)
        print(f"{total} órdenes escritas en Supabase.", flush=True)
        completo = all(por_filial.get(f, 0) > 0 for f in FILIALES) and not ant_incompleto
        borradas = 0 if ant_incompleto else subir.borrar_ausentes(set(filas_por_id), estado, desde.isoformat(), completo)
        if borradas:
            print(f"{borradas} órdenes que ya no están en el reporte se borraron de la base.", flush=True)
        if st_hilo is not None:   # el Speed Test termina antes de guardar el estado y los tableros
            st_hilo.join(timeout=240)
            tableros["speedtest_vel"] = {"v": estado.get("stv", {})}   # copia permanente (el portal no la lee; solo el robot)
            # enlaces de cada instalación «FILIAL|OS» → [día, [[enlace, título]]] (para reportes; el portal no la lee)
            tableros["speedtest_enlaces"] = {"o": tableros["speedtest"].pop("_enlaces", {})}
        if "speedtest" in tableros:
            tableros["speedtest"].pop("_enlaces", None)   # nunca va al portal
        estado["arranque"] = False
        subir.guardar_estado(ESTADO, estado)
        # Mes anterior (cerrado): solo se reescribe en la actualización completa (cada ~2 h); en las rápidas y exprés sus
        # datos vienen de esa misma completa, así que reescribirlo cada 10 min solo gastaba disco de Supabase.
        n_tab = subir.guardar_tableros(tableros, estado, inicio,
                                       omitir=None if not args.base else (lambda c: bool(_RE_MES_CERRADO.search(c))))
        try:
            subir.limpiar_diagnosticos()
        except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
            print(f"Aviso: no se limpiaron los diagnósticos: {e}", flush=True)
        subir.guardar_estado(ESTADO, estado)
        print(f"{n_tab} de {len(tableros)} tableros cambiaron y se guardaron.", flush=True)
        print(f"[tiempo] guardado: {time.time() - t_tab:.0f} s · total {time.time() - t_ini:.0f} s", flush=True)
        subir.registrar_corrida("periodo", total, True, f"{'exprés · ' if args.parche and not args.excel else ''}{'SIN MES ANTERIOR · ' if ant_incompleto else ''}ok · {len(filas)} en reporte · {borradas} borradas · {n_tab} tableros · {_tiempos(args.corte, t_ini, t_proc, t_tab)}",
                                inicio, _ahora())
        return 0
    except Exception as e:  # noqa: BLE001 - se registra el fallo y el job termina en rojo para que llegue el aviso
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        if not args.sin_subir:
            try:
                from . import subir

                subir.registrar_corrida("periodo", total or None, False, str(e), inicio, _ahora())
            except Exception:  # noqa: BLE001
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
