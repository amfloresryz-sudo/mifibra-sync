"""Punto de entrada del robot.

  python -m sync.main                         # una corrida: descarga el periodo, calcula y guarda lo que cambió
  python -m sync.main --excel archivo.xlsx    # usa un Excel ya descargado (sin robot)
  python -m sync.main --excel a.xlsx --sin-subir   # solo procesa y calcula, no escribe en Supabase
  python -m sync.main --solo-descargar r.xlsx      # solo descarga el reporte (el turno descarga y procesa en paralelo)
  python -m sync.main --excel r.xlsx --corte ISO   # procesa un reporte ya descargado, con la hora en que se pidió

Cada corrida descarga UN reporte, igual que a mano en ScordSoft:
  Cronograma de Trabajo (Órdenes de Servicio) · Filial SELECCIONE (todas) · por Registro
  · del 1 del mes anterior al último día del mes en curso.

Con ese único archivo se calcula todo en memoria (tableros de Instalaciones y Averías de cada filial, mes en curso y
mes anterior, e indicadores de la portada). Supabase solo recibe lo que cambió; no se relee la base.
"""
from __future__ import annotations

import argparse
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


def _cols(df: pd.DataFrame, columnas: str, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    """Subconjunto con las columnas que antes se pedían a la base (las que falten se agregan vacías)."""
    nombres = [*extra, *columnas.split(",")]
    x = df.reindex(columns=nombres)
    return x


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
    for filial, d in grupos:
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
            datos["filial"] = filial
            tableros[f"observadas:{filial}"] = {"obs": datos.pop("obs"), "filial": filial, "corte": datos["corte"]}
            tableros[f"instalaciones:{filial}"] = datos
        print(f"  Tableros de {filial} calculados en {time.time() - t0:.0f} s.", flush=True)
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
    args = ap.parse_args(argv)

    if args.solo_descargar:
        # Turno en paralelo: la descarga (lo más lento, ~14 min) corre mientras se procesa el reporte anterior.
        from .descargar import descargar_reportes

        hoy = datetime.now(LIMA).date()
        d0, d1 = periodo(hoy)
        t0 = time.time()
        try:
            # Se descarga en una carpeta aparte: así el procesador nunca ve archivos a medio bajar.
            # Si ScordSoft entrega solo LIMA, las demás filiales llegan en archivos extra: van a «partes/<marca>_N.xlsx»
            # y el procesador los junta con el principal.
            carpeta = Path(tempfile.mkdtemp(prefix="scordsoft_"))
            r = descargar_reportes([(carpeta / "reporte.xlsx", d0, d1, "TODAS", "registro")])
            partes = args.solo_descargar.parent / "partes"
            marca = args.solo_descargar.name.split(".")[0]
            for i, extra in enumerate(r[1:]):
                partes.mkdir(exist_ok=True)
                shutil.move(str(extra), str(partes / f"{marca}_{i}.xlsx"))
            shutil.move(str(r[0]), str(args.solo_descargar))
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
        if args.excel:
            archivos = list(args.excel)
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

            indice_cajas = cajas.cargar()
            if indice_cajas:
                print(f"Cajas de planta cargadas: {indice_cajas.n}.", flush=True)
        except Exception as e:  # noqa: BLE001 - sin mapas se conservan las cajas ya guardadas
            print(f"Aviso: no se cargaron los mapas de cajas: {e}", flush=True)
        filas_por_id: dict[int, dict] = {}
        links_st: dict[int, list] = {}
        for archivo in archivos:
            df0 = procesar.leer_excel(archivo)
            try:
                links_st.update(procesar.speedtest_por_os(df0))
            except Exception as e:  # noqa: BLE001 - Speed Test nunca detiene la corrida
                print(f"Aviso: no se leyeron los enlaces de Speedtest: {e}", flush=True)
            for fila in procesar.a_filas(df0, inicio, indice_cajas):
                filas_por_id[fila["id_os"]] = fila
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
        tableros = calcular_tableros(df, ahora, inicio, links_st)
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
        total = subir.upsert_ordenes(filas, estado, cajas_ok=indice_cajas is not None)
        print(f"{total} órdenes escritas en Supabase.", flush=True)
        completo = all(por_filial.get(f, 0) > 0 for f in FILIALES)
        borradas = subir.borrar_ausentes(set(filas_por_id), estado, desde.isoformat(), completo)
        if borradas:
            print(f"{borradas} órdenes que ya no están en el reporte se borraron de la base.", flush=True)
        estado["arranque"] = False
        subir.guardar_estado(ESTADO, estado)
        n_tab = subir.guardar_tableros(tableros, estado, inicio)
        try:
            subir.limpiar_diagnosticos()
        except Exception as e:  # noqa: BLE001 - nunca detiene la corrida
            print(f"Aviso: no se limpiaron los diagnósticos: {e}", flush=True)
        subir.guardar_estado(ESTADO, estado)
        print(f"{n_tab} de {len(tableros)} tableros cambiaron y se guardaron.", flush=True)
        print(f"[tiempo] guardado: {time.time() - t_tab:.0f} s · total {time.time() - t_ini:.0f} s", flush=True)
        subir.registrar_corrida("periodo", total, True, f"ok · {len(filas)} en reporte · {borradas} borradas · {n_tab} tableros",
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
