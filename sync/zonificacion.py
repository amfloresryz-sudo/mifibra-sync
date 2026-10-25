"""Tablero de Zonificación: todos los distritos con sus planos.
- Catálogo guardado (ajustes «zonificacion:VISTOS», lo escribe solo el robot): cada distrito + plano que aparece alguna vez
  queda guardado para siempre con la fecha en que se vio por primera vez; si sale un plano nuevo se agrega solo.
- EECC asignada: la que se editó en el portal (ajustes «zonificacion:GLOBAL», clave master) o, si no, la de la hoja ZONA de
  DATOS_TEC (solo Lima). Un plano nuevo sin EECC queda marcado para asignarle una.
- Actividad: instalaciones y averías del periodo, pendientes de hoy, última orden y lo del mes en curso para los promedios."""
import pandas as pd

from . import instalaciones
from .zonas import ZONAS

COLS = ["fi", "zo", "di", "pl", "pt", "ec", "ins", "av", "pen", "ult", "cat", "ing_m", "liq_m", "obs_m", "anu_m", "k", "alta", "ed"]
# ing_m · liq_m · obs_m · anu_m = instalaciones del mes en curso: ingresadas (F. Recep), liquidadas (DESCARGADA por F. Atención),
# observadas y anuladas (por F. Recep). La página las divide entre los días transcurridos del mes para el promedio por día.
# k = clave del plano (FILIAL|DISTRITO|PLANO normalizados) · alta = día en que se vio por primera vez · ed = 1 si la EECC se editó en el portal
CLAVE_VISTOS, CLAVE_EDIT = "zonificacion:VISTOS", "zonificacion:GLOBAL"


def _t(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _dia_lima(s: pd.Series) -> pd.Series:
    """F. Recep (UTC en la base) → día en hora de Lima, «AAAA-MM-DD» ('' si no hay)."""
    t = pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert("America/Lima")
    return t.dt.strftime("%Y-%m-%d").fillna("")


def clave(fi, di, pl) -> str:
    n = instalaciones._norm
    return f"{_t(fi).upper()}|{n(di)}|{n(pl)}"


def construir(df: pd.DataFrame, corte: str, vistos: dict | None = None, editadas: dict | None = None) -> tuple[dict, dict]:
    """Devuelve (tablero, vistos actualizados). vistos = {clave: [filial, distrito, plano, primera_vez]}; editadas = {clave: EECC}."""
    norm, plantas = instalaciones._norm, instalaciones.CONFIG["plantas"]
    vistos = dict(vistos or {})
    editadas = editadas or {}
    # Mes en curso y días transcurridos (hora de Lima): salen de la hora de la actualización
    hoy = _dia_lima(pd.Series([corte])).iat[0] if corte else ""
    mes, dias = (hoy[:7], int(hoy[8:10])) if hoy else ("", 1)
    zn = {norm(d): z for d, z in ZONAS.items()}
    filas: dict[str, dict] = {}
    nuevos: set[str] = set()

    def fila(fi, di, pl, primera=""):
        k = clave(fi, di, pl)
        if k not in filas:
            filas[k] = {"fi": fi, "di": di, "pl": pl, "ins": 0, "av": 0, "pen": 0, "ult": "", "cat": 0,
                        "ing_m": 0, "liq_m": 0, "obs_m": 0, "anu_m": 0}
        if k not in vistos:   # plano nuevo: se guarda con la fecha en que se vio por primera vez
            vistos[k] = [fi, di, pl, primera or hoy]; nuevos.add(k)
        elif k in nuevos and primera[:1].isdigit() and (not vistos[k][3][:1].isdigit() or primera < vistos[k][3]):
            vistos[k][3] = primera     # nuevo en esta corrida: la fecha de su orden más antigua
        return filas[k]

    # 1) Todo lo guardado antes (aunque ya no tenga órdenes en el periodo)
    for fi, di, pl, _ in list(vistos.values()):
        fila(fi, di, pl)
    # 2) Catálogo de la hoja ZONA (Lima): distrito|plano → EECC
    for k0, ec in instalaciones.PLANOS_EECC.items():
        di, pl = k0.split("|", 1)
        f = fila("LIMA", di, pl, primera="hoja ZONA")
        f["cat"], f["ec"] = 1, ec

    # 3) Planos vistos en las órdenes del periodo
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
            f = fila(fi, di or "SIN DISTRITO", pl, primera=fr)
            if di and norm(di) == norm(f["di"]):
                f["di"] = di          # nombre del distrito tal como viene en ScordSoft (con Ñ)
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
    for k, f in filas.items():
        vistos[k] = [f["fi"], f["di"], f["pl"], vistos[k][3]]     # nombre más reciente, misma fecha de alta
        zona = (zn.get(norm(f["di"]), "SIN ZONA") if f["fi"] == "LIMA" else f["fi"])
        hoja = f.get("ec") or (instalaciones.eecc_del_plano(f["di"], f["pl"]) if f["fi"] == "LIMA" else "") or ""
        ed = k in editadas
        out.append([f["fi"], zona, f["di"], f["pl"], instalaciones._planta(f["pl"], plantas), editadas[k] if ed else hoja,
                     f["ins"], f["av"], f["pen"], f["ult"], f["cat"], f["ing_m"], f["liq_m"], f["obs_m"], f["anu_m"],
                     k, vistos[k][3], int(ed)])
    out.sort(key=lambda r: (r[0] != "LIMA", r[0], r[2], r[3]))
    return {"cols": COLS, "filas": out, "corte": corte, "mes": mes, "dias": dias}, vistos


def actualizar(df: pd.DataFrame, corte: str) -> dict:
    """Lee lo guardado (planos vistos y EECC editadas), arma el tablero y guarda los planos nuevos.
    Si no se pudo leer lo guardado, no se escribe nada (para no borrar el catálogo)."""
    from . import subir
    leido, vistos, editadas = True, {}, {}
    try:
        r = subir._pedir("GET", subir._url("ajustes") + "?select=clave,datos&clave=in.(%22" + CLAVE_VISTOS + "%22,%22" + CLAVE_EDIT + "%22)",
                         "Zonificación (leer)", headers=subir._cabeceras())
        for fila in r.json():
            d = fila.get("datos") or {}
            if fila["clave"] == CLAVE_VISTOS:
                vistos = d.get("p") or {}
            else:
                editadas = d.get("eecc") or {}
    except Exception as e:  # noqa: BLE001
        leido = False
        print(f"Aviso: no se leyó el catálogo de Zonificación: {e}", flush=True)
    antes = dict(vistos)
    tablero, vistos = construir(df, corte, vistos, editadas)
    if leido and vistos != antes:
        subir._pedir("POST", subir._url("ajustes") + "?on_conflict=clave", "Zonificación (guardar)",
                     json={"clave": CLAVE_VISTOS, "datos": {"p": vistos}, "actualizado": corte},
                     headers=subir._cabeceras({"Prefer": "resolution=merge-duplicates,return=minimal"}))
        print(f"  Zonificación: {len(vistos) - len(antes)} planos nuevos guardados ({len(vistos)} en total).", flush=True)
    return tablero
