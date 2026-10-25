"""Tablero de Zonificación: todos los planos de cada distrito.
Junta el catálogo de planos (hoja ZONA de DATOS_TEC: distrito + plano → EECC asignada, solo Lima) con los planos que
aparecen en las órdenes del periodo (todas las filiales). Para cada plano: filial, zona, distrito, planta, EECC asignada,
instalaciones y averías del periodo, pendientes de hoy y la fecha de la última orden. Sin leer Supabase."""
import pandas as pd

from . import instalaciones
from .zonas import ZONAS

COLS = ["fi", "zo", "di", "pl", "pt", "ec", "ins", "av", "pen", "ult", "cat", "ing_m", "liq_m", "obs_m", "anu_m"]
# ing_m · liq_m · obs_m · anu_m = instalaciones del mes en curso: ingresadas (F. Recep), liquidadas (DESCARGADA por F. Atención),
# observadas y anuladas (por F. Recep). La página las divide entre los días transcurridos del mes para el promedio por día.


def _t(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _dia_lima(s: pd.Series) -> pd.Series:
    """F. Recep (UTC en la base) → día en hora de Lima, «AAAA-MM-DD» ('' si no hay)."""
    t = pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert("America/Lima")
    return t.dt.strftime("%Y-%m-%d").fillna("")


def construir(df: pd.DataFrame, corte: str) -> dict:
    norm, plantas = instalaciones._norm, instalaciones.CONFIG["plantas"]
    # Mes en curso y días transcurridos (hora de Lima): salen de la hora de la actualización
    ult = _dia_lima(pd.Series([corte])).iat[0] if corte else ""
    mes, dias = (ult[:7], int(ult[8:10])) if ult else ("", 1)
    zn = {norm(d): z for d, z in ZONAS.items()}
    filas: dict[tuple, dict] = {}

    def fila(fi, di, pl):
        k = (fi, norm(di), norm(pl))
        if k not in filas:
            filas[k] = {"fi": fi, "di": di, "pl": pl, "ins": 0, "av": 0, "pen": 0, "ult": "", "cat": 0, "ing_m": 0, "liq_m": 0, "obs_m": 0, "anu_m": 0}
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
        fecha = _dia_lima(x["fecha_registro"])
        fat = x["fecha_atencion"].map(_t).str[:10] if "fecha_atencion" in x else pd.Series("", index=x.index)
        for fi, di, pl, i, a, e, fr, fa in zip(x["filial"].map(_t), x["distrito"].map(_t), x["plano"].map(_t), es_i, es_a, est, fecha, fat):
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
            if i:
                rm, am = fr[:7] == mes, fa[:7] == mes
                f["ing_m"] += int(rm)
                f["liq_m"] += int(am and e == "DESCARGADA")
                f["obs_m"] += int(rm and e == "OBSERVADA")
                f["anu_m"] += int(rm and e == "ANULADA")

    out = []
    for f in filas.values():
        zona = (zn.get(norm(f["di"]), "SIN ZONA") if f["fi"] == "LIMA" else f["fi"])
        ec = f.get("ec") or (instalaciones.eecc_del_plano(f["di"], f["pl"]) if f["fi"] == "LIMA" else "") or ""
        out.append([f["fi"], zona, f["di"], f["pl"], instalaciones._planta(f["pl"], plantas), ec,
                    f["ins"], f["av"], f["pen"], f["ult"], f["cat"], f["ing_m"], f["liq_m"], f["obs_m"], f["anu_m"]])
    out.sort(key=lambda r: (r[0] != "LIMA", r[0], r[2], r[3]))
    return {"cols": COLS, "filas": out, "corte": corte, "mes": mes, "dias": dias}
