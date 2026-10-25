"""Tablero de Zonificación: todos los planos de cada distrito.
Junta el catálogo de planos (hoja ZONA de DATOS_TEC: distrito + plano → EECC asignada, solo Lima) con los planos que
aparecen en las órdenes del periodo (todas las filiales). Para cada plano: filial, zona, distrito, planta, EECC asignada,
instalaciones y averías del periodo, pendientes de hoy y la fecha de la última orden. Sin leer Supabase."""
import pandas as pd

from . import instalaciones
from .zonas import ZONAS

COLS = ["fi", "zo", "di", "pl", "pt", "ec", "ins", "av", "pen", "ult", "cat"]


def _t(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def construir(df: pd.DataFrame, corte: str) -> dict:
    norm, plantas = instalaciones._norm, instalaciones.CONFIG["plantas"]
    zn = {norm(d): z for d, z in ZONAS.items()}
    filas: dict[tuple, dict] = {}

    def fila(fi, di, pl):
        k = (fi, norm(di), norm(pl))
        if k not in filas:
            filas[k] = {"fi": fi, "di": di, "pl": pl, "ins": 0, "av": 0, "pen": 0, "ult": "", "cat": 0}
        return filas[k]

    # 1) Catálogo (Lima): distrito|plano → EECC
    for clave, ec in instalaciones.PLANOS_EECC.items():
        di, pl = clave.split("|", 1)
        f = fila("LIMA", di, pl)
        f["cat"], f["ec"] = 1, ec

    # 2) Planos vistos en las órdenes del periodo
    if df is not None and not df.empty and "plano" in df:
        x = df[df["plano"].map(_t) != ""]
        tipo, est = x["tipo_orden"].map(_t), x["estado_os"].map(_t)
        es_i = tipo == "INSTALACION SERVICIO"
        es_a = tipo == "SERVICIO TECNICO"
        fecha = x["fecha_registro"].map(_t).str[:10]
        for fi, di, pl, i, a, e, fr in zip(x["filial"].map(_t), x["distrito"].map(_t), x["plano"].map(_t), es_i, es_a, est, fecha):
            if not fi or fi == "SIN FILIAL":
                continue
            f = fila(fi, di or "SIN DISTRITO", pl)
            if di and not f["cat"]:
                f["di"] = di          # nombre del distrito tal como viene en ScordSoft (con Ñ)
            elif di and f["cat"] and norm(di) == norm(f["di"]):
                f["di"] = di
            f["ins"] += int(i)
            f["av"] += int(a)
            f["pen"] += int(i and e == "REGISTRADA")
            if fr > f["ult"]:
                f["ult"] = fr

    out = []
    for f in filas.values():
        zona = (zn.get(norm(f["di"]), "SIN ZONA") if f["fi"] == "LIMA" else f["fi"])
        ec = f.get("ec") or (instalaciones.eecc_del_plano(f["di"], f["pl"]) if f["fi"] == "LIMA" else "") or ""
        out.append([f["fi"], zona, f["di"], f["pl"], instalaciones._planta(f["pl"], plantas), ec,
                    f["ins"], f["av"], f["pen"], f["ult"], f["cat"]])
    out.sort(key=lambda r: (r[0] != "LIMA", r[0], r[2], r[3]))
    return {"cols": COLS, "filas": out, "corte": corte}
