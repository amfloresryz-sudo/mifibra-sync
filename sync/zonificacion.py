"""Tablero de Zonificación: todos los distritos con sus planos.
- Catálogo guardado (ajustes «zonificacion:VISTOS», lo escribe solo el robot): cada distrito + plano que aparece alguna vez
  queda guardado para siempre con la fecha en que se vio por primera vez; si sale un plano nuevo se agrega solo.
- EECC asignada: la que se editó en el portal (ajustes «zonificacion:GLOBAL», clave master) o, si no, la de la hoja ZONA de
  DATOS_TEC (solo Lima). Un plano nuevo sin EECC queda marcado para asignarle una.
- Actividad: instalaciones y averías del periodo, pendientes de hoy, última orden y lo del mes en curso para los promedios."""
import numpy as np
import pandas as pd

from . import instalaciones
from .zonas import ZONAS

COLS = ["fi", "zo", "di", "pl", "pt", "ec", "ins", "av", "pen", "ult", "cat", "ing_m", "liq_m", "obs_m", "anu_m", "k", "alta", "ed", "tt"]
# tt = por tipo de trabajo (índice en «tipos»): [órdenes del periodo, pendientes, ingresadas del mes, liquidadas del mes, observadas del mes, anuladas del mes, última orden]
TIPO_TXT = {"INSTALACION SERVICIO": "INSTALACIONES", "SERVICIO TECNICO": "AVERÍAS"}
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
    t = pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert("America/Lima").dt.tz_localize(None)
    d = np.datetime_as_string(t.values.astype("datetime64[D]"))   # mucho más rápido que strftime con cientos de miles de filas
    return pd.Series(np.where(t.isna().values, "", d), index=s.index)


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

    # 3) Planos vistos en las órdenes del periodo (agrupado con pandas: el periodo trae cientos de miles de órdenes)
    if df is not None and not df.empty and "plano" in df:
        tx = lambda c: df[c].fillna("").astype(str).str.strip() if c in df else pd.Series("", index=df.index)  # noqa: E731
        x = pd.DataFrame({"fi": tx("filial"), "di": tx("distrito"), "pl": tx("plano"), "tp": tx("tipo_orden"), "e": tx("estado_os")})
        x = x[(x["pl"] != "") & (x["fi"] != "") & (x["fi"] != "SIN FILIAL")]
        x["di"] = x["di"].replace("", "SIN DISTRITO")
        x["fr"] = _dia_lima(df.loc[x.index, "fecha_registro"])
        x["fa"] = tx("fecha_atencion").loc[x.index].str[:10]
        x["tp"] = x["tp"].replace({**TIPO_TXT, "": "SIN TIPO"})
        # Instalaciones = solo servicio INTERNET (igual que el tablero de Instalaciones); las de otros servicios (p. ej. SERVICIO OTT)
        # van como su propio tipo de trabajo, que se puede sumar desde el segmentador «Tipo de trabajo»
        if "servicio" in df:
            sv = df.loc[x.index, "servicio"].fillna("INTERNET").astype(str).str.strip().str.upper().replace("", "INTERNET")
            otro = (x["tp"] == "INSTALACIONES") & (sv != "INTERNET")
            x.loc[otro, "tp"] = "INSTALACIONES " + sv[otro].str.replace(r"^SERVICIO\s+", "", regex=True)
        nd = {v: norm(v) for v in x["di"].unique()}
        npl = {v: norm(v) for v in x["pl"].unique()}
        x["k"] = x["fi"].str.upper() + "|" + x["di"].map(nd) + "|" + x["pl"].map(npl)
        rm, am = x["fr"].str[:7] == mes, x["fa"].str[:7] == mes
        x = x.assign(n=1, pen=(x["e"] == "REGISTRADA").astype(int), ing=rm.astype(int), liq=(am & (x["e"] == "DESCARGADA")).astype(int),
                     obs=(rm & (x["e"] == "OBSERVADA")).astype(int), anu=(rm & (x["e"] == "ANULADA")).astype(int))
        g = x.groupby(["k", "tp"]).agg(n=("n", "sum"), pen=("pen", "sum"), ing=("ing", "sum"), liq=("liq", "sum"),
                                       obs=("obs", "sum"), anu=("anu", "sum"), ult=("fr", "max"))
        cab = x.groupby("k").agg(fi=("fi", "first"), di=("di", "first"), pl=("pl", "first"), primera=("fr", "min"))
        for k, c in cab.iterrows():
            f = fila(c["fi"], c["di"], c["pl"], primera=c["primera"])
            if norm(c["di"]) == norm(f["di"]):
                f["di"] = c["di"]          # nombre del distrito tal como viene en ScordSoft (con Ñ)
        for (k, tp), v in g.iterrows():
            f = filas[k]
            t = [int(v["n"]), int(v["pen"]), int(v["ing"]), int(v["liq"]), int(v["obs"]), int(v["anu"]), v["ult"] or ""]
            f.setdefault("tt", {})[tp] = t
            if t[6] > f["ult"]:
                f["ult"] = t[6]
            if tp == "INSTALACIONES":
                f["ins"], f["pen"], f["ing_m"], f["liq_m"], f["obs_m"], f["anu_m"] = t[0], t[1], t[2], t[3], t[4], t[5]
            elif tp == "AVERÍAS":
                f["av"] = t[0]

    # Tipos de trabajo: instalaciones primero, luego averías y el resto por cantidad
    cnt: dict[str, int] = {}
    for f in filas.values():
        for t, v in (f.get("tt") or {}).items():
            cnt[t] = cnt.get(t, 0) + v[0]
    tipos = sorted(cnt, key=lambda t: (t != "INSTALACIONES", t != "AVERÍAS", -cnt[t], t))
    ti = {t: i for i, t in enumerate(tipos)}
    out = []
    for k, f in filas.items():
        vistos[k] = [f["fi"], f["di"], f["pl"], vistos[k][3]]     # nombre más reciente, misma fecha de alta
        zona = (zn.get(norm(f["di"]), "SIN ZONA") if f["fi"] == "LIMA" else f["fi"])
        hoja = f.get("ec") or (instalaciones.eecc_del_plano(f["di"], f["pl"]) if f["fi"] == "LIMA" else "") or ""
        ed = k in editadas
        out.append([f["fi"], zona, f["di"], f["pl"], instalaciones._planta(f["pl"], plantas), editadas[k] if ed else hoja,
                     f["ins"], f["av"], f["pen"], f["ult"], f["cat"], f["ing_m"], f["liq_m"], f["obs_m"], f["anu_m"],
                     k, vistos[k][3], int(ed), {ti[t]: v for t, v in (f.get("tt") or {}).items()}])
    out.sort(key=lambda r: (r[0] != "LIMA", r[0], r[2], r[3]))
    return {"cols": COLS, "filas": out, "corte": corte, "mes": mes, "dias": dias, "tipos": tipos}, vistos


def asistencia(tableros: dict | None) -> dict:
    """Asistencia de rutas del mes en curso por filial y EECC, de las liquidaciones del tablero de Instalaciones:
    {filial: {"dias": días con liquidaciones, "ec": {EECC: [liquidadas, técnico-días]}}}. técnico-días = suma, día por día,
    de los técnicos distintos que liquidaron (un técnico que liquida 3 el mismo día cuenta 1)."""
    out = {}
    for clave_t, t in (tableros or {}).items():
        if not clave_t.startswith("instalaciones:") or clave_t.count(":") != 1 or clave_t.endswith(":PROVINCIAS"):
            continue
        fi, liq = clave_t.split(":", 1)[1], t.get("liq") or []
        dias, por = set(), {}
        for x in liq:
            d, ec, tec = x.get("d"), _t(x.get("ec")) or "SIN EECC", _t(x.get("tec"))
            if d is None:
                continue
            dias.add(d)
            e = por.setdefault(ec, [0, set()])
            e[0] += 1
            if tec:
                e[1].add((d, tec))
        out[fi] = {"dias": len(dias), "ec": {ec: [v[0], len(v[1])] for ec, v in por.items()}}
    return out


def actualizar(df: pd.DataFrame, corte: str, tableros: dict | None = None) -> dict:
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
    try:
        tablero["asis"] = asistencia(tableros)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se calculó la asistencia de rutas: {e}", flush=True)
    return tablero
