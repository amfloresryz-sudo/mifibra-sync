"""Calcula el tablero de Averías y TC a partir de la base de datos y lo guarda en `tableros`.

Universo: órdenes cuyo tipo (DETALLE de TIPO_AB.xlsx) es AVERIA, TC o un misceláneo (TRASLADO INT, TRASLADO EXT,
REINSTALACION, CAMBIO DE EQUIPO; en el JSON t = "MISC" y ts = el detalle).
El resultado es un JSON con cuatro listas que el portal (public/averias.html) dibuja tal cual:

  liq   averías, TC y misceláneos liquidados (DESCARGADA) en el mes, por fecha de atención (t = tipo)
  cien  averías (sin TC) recibidas en el mes, con su clasificación frente a la meta de 100 min
  ing   ingresos del mes (averías y TC) por día y distrito
  pend  órdenes pendientes (REGISTRADA) con su estado individual
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pandas as pd

from .zonas import ZONAS

SLA = 100
# Misceláneos (mismo grupo que el tablero de pendientes): todo lo que no es avería, TC ni instalación.
MISC = {"TRASLADO INT", "TRASLADO EXT", "REINSTALACION", "CAMBIO DE EQUIPO"}
TIPOS_ORDEN_MISC = ["TRASLADO INTERNO", "TRASLADO EXTERNO", "REINSTALACION SERVICIO", "CAMBIO DE EQUIPO"]
TZ = "America/Lima"
COLUMNAS_BD = (
    "id_os,nro_os,estado_os,abonado_codigo,distrito,segmento,servicio,motivo,lugar_averia,fecha_registro,hora_recepcion,"
    "fecha_atencion,atencion_inicio,atencion_fin,horario_atencion,tecnico_ejecutor,tecnico_responsable,contrata,"
    "causa,sub_causa,solucion,fecha_instalacion_abonado,obs_agenda,agenda_estado,agenda_texto,"
    "tarifa_servicio,plano,tipo_vivienda,zona_fftt,nap,borne,dispositivo,detalle,canal_atencion,fecha_programada,observaciones,obs_visita_todo,obs_visita_en,coordenadas,inicio_fachada"
)
# Instalaciones del abonado (para el detalle de Infancia).
COLUMNAS_INST = "abonado_codigo,nro_os,fecha_atencion,contrata,tecnico_ejecutor,distrito,canal_atencion"
_RE_FRANJA = re.compile(r"\d{1,2}\s*-\s*\d{1,2}")

# Gráfico de 100 min: solo estas combinaciones (SERVICIO TECNICO · LUGAR AVERIA = RED CLIENTE · detalle AVERIA).
MOTIVOS_100 = {"SIN SERVICIO DE INTERNET", "RETENCIONES", "MESA CONTROL DESPACHO", "LENTITUD", "PROBLEMA TV", "CLIENTE ONLINE",
               "PROBLEMAS CON JUEGOS", "INTERMITENCIA", "RED CLIENTE", "ALCANCE DE WIFI", "PROBLEMA CON ACCESORIOS"}


def entra_100(motivo, lugar, servicio) -> bool:
    m, l, s = _txt(motivo).upper(), _txt(lugar).upper(), _txt(servicio).upper()
    if l != "RED CLIENTE" or m not in MOTIVOS_100:
        return False
    return s == "INTERNET" or (s == "SERVICIO OTT" and m == "PROBLEMA TV")


# 1.ª / 2.ª visita o VD (visita directa) en las observaciones: tampoco entra al cálculo de 100 min.
_RE_VISITA = re.compile(r"(?:(?:PRIMERA|SEGUNDA|[12])\s*(?:ERA|RA|ER|DA|DO|°|º|\.ª|ª)?\s*VISI?T[AO]S?\b|VISITA\s+DIRECTA\b|\bVD\b)", re.I)
_CANALES = {"INTEGRAL DE CAMPO": "IC", "VENTAS INBOUND": "VI"}


def _canal(v) -> str:
    """Grupo del CANAL DE ATENCIÓN: IC (Integral de campo), VI (Ventas inbound) u OT (otros canales / vacío)."""
    return _CANALES.get(_txt(v).strip().upper(), "OT")


def _lima(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, utc=True, errors="coerce").dt.tz_convert(TZ).dt.tz_localize(None)


def _txt(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT else str(v)


def _contrata(s) -> str | None:
    s = _txt(s)
    if not s:
        return None
    return s.replace("EECC ", "").replace(" TELECOM SAC", "").replace(" TELECOM", "").replace("SEGANETWORKS", "SEGANET")


def _eecc_plano(distrito, plano) -> str | None:
    """EECC que le corresponde al plano (DATOS_TEC), igual que en instalaciones."""
    try:
        from .instalaciones import eecc_del_plano
        return eecc_del_plano(distrito, plano)
    except Exception:
        return None


def _iso(x) -> str | None:
    return x.strftime("%Y-%m-%dT%H:%M:00") if pd.notna(x) else None




def construir(df: pd.DataFrame, tec_contrata: dict[str, str], corte: datetime, zonas: dict[str, str] = ZONAS,
              inst: pd.DataFrame | None = None) -> dict:
    """df: órdenes SERVICIO TECNICO (cualquier lugar) desde el inicio del mes anterior, con las columnas de la BD.
    inst: instalaciones DESCARGADA (COLUMNAS_INST) para mostrar la OS de instalación en el detalle de Infancia."""
    _zona = lambda d: zonas.get(_txt(d).upper().strip(), "SIN ZONA")  # noqa: E731
    corte = pd.Timestamp(corte).tz_localize(None) if pd.Timestamp(corte).tzinfo else pd.Timestamp(corte)
    anio, mes = corte.year, corte.month

    df = df.copy()
    # Tipo según TIPO_AB.xlsx (columna DETALLE): solo cuentan las órdenes cuyo detalle es AVERIA o TC.
    # Mientras la columna aún no esté llenada en la base (antes de la corrida completa) se usa la regla anterior.
    det = df["detalle"] if "detalle" in df else pd.Series([None] * len(df), index=df.index)
    if det.map(_txt).astype(bool).any():
        df["tipo"] = det.map(_txt)
    else:
        df["tipo"] = [("TC" if _txt(m).startswith("TC") else "AVERIA") if _txt(l) == "RED CLIENTE" else "" for m, l in zip(df["motivo"], df["lugar_averia"])]
    df["ts"] = df["tipo"].where(df["tipo"].isin(MISC), "")
    df.loc[df["tipo"].isin(MISC), "tipo"] = "MISC"
    reg = _lima(df["fecha_registro"])
    df["fr"] = reg.dt.normalize()
    df["start"] = reg.where(df["hora_recepcion"].notna() & (df["hora_recepcion"].astype(str) != ""))
    df["rm"] = df["start"].dt.hour * 60 + df["start"].dt.minute
    df["fa"] = pd.to_datetime(df["fecha_atencion"], errors="coerce")
    # Inicio real de la atención: FechaInicioFachada (fecha y hora en que el técnico inició). ScordSoft arma
    # «F. Atenc. + Hora I» con la fecha de la ÚLTIMA visita, así que si la atención se cerró al día siguiente el
    # inicio salía con un día de más (ej. OS 7366: inició 12-oct 18:36 y aparecía 13-oct 18:36 → «No cumplió»).
    ini_bd = _lima(df["atencion_inicio"])
    ifa = _lima(df["inicio_fachada"]).dt.floor("min") if "inicio_fachada" in df else pd.Series(pd.NaT, index=df.index)
    ifa = ifa.where(ifa >= reg.dt.floor("min"))          # una fachada anterior a la recepción no vale: se usa la de la BD
    df["hi"] = ifa.combine_first(ini_bd)
    df["hf"] = _lima(df["atencion_fin"])
    df["end"] = df["hi"].dt.floor("min")
    df["mins"] = (df["end"] - df["start"]).dt.total_seconds() / 60
    df["tmi"] = (df["hf"] - df["hi"]).dt.total_seconds()

    # Reiteradas: mismo abonado con otra orden de servicio técnico (no anulada) en los 30 días previos.
    base = df[(df["estado_os"] != "ANULADA") & (df["tipo"] != "MISC")][["abonado_codigo", "id_os", "fr"]]
    grupos = {k: v for k, v in base.groupby("abonado_codigo")}

    def reiteradas(ab, ido, fr) -> int:
        h = grupos.get(ab)
        if h is None or pd.isna(fr):
            return 0
        dd = (fr - h["fr"]).dt.days
        return int(((h["id_os"] != ido) & ((dd > 0) | ((dd == 0) & (h["id_os"] < ido))) & (dd <= 30)).sum())

    def anteriores(ab, ido, fr) -> list[dict]:
        """OS de avería/TC del mismo abonado (no anuladas) recibidas en los 30 días previos, más antiguas primero."""
        h = historial.get(ab)
        if h is None or pd.isna(fr):
            return []
        dd = (fr - h["fr"]).dt.days
        h = h[(h["id_os"] != ido) & ((dd > 0) | ((dd == 0) & (h["id_os"] < ido))) & (dd <= 30)].sort_values("fr")
        return [{"os": int(r["nro_os"]), "t": r["tipo"], "fr": r["fr"].strftime("%Y-%m-%d"), "e": _txt(r["estado_os"]),
                 "ec": _contrata(r["contrata"]) or "", "tec": _txt(r["tecnico_ejecutor"]) or _txt(r["tecnico_responsable"]),
                 "ca": _txt(r["causa"]), "sol": _txt(r["solucion"])} for _, r in h.iterrows()]

    historial = {k: v for k, v in df[(df["estado_os"] != "ANULADA") & (df["tipo"] != "MISC")].groupby("abonado_codigo")}
    instal = {}
    if inst is not None and len(inst):
        i2 = inst.assign(fa=pd.to_datetime(inst["fecha_atencion"], errors="coerce")).dropna(subset=["fa"]).sort_values("fa")
        for ab, g in i2.groupby("abonado_codigo"):
            instal[ab] = g

    def instalacion(ab, fr):
        g = instal.get(ab)
        if g is None or pd.isna(fr):
            return None
        g = g[g["fa"] <= fr]
        if not len(g):
            return None
        r = g.iloc[-1]
        return {"os": int(r["nro_os"]), "f": r["fa"].strftime("%Y-%m-%d"), "ec": _contrata(r["contrata"]) or "",
                "tec": _txt(r["tecnico_ejecutor"])}

    st = df[df["tipo"].isin(["AVERIA", "TC", "MISC"])].copy()
    # TMR: recepción → fin de la atención (minutos). Si el fin es anterior a la recepción no vale.
    st["tmr"] = (st["hf"].dt.floor("min") - st["start"]).dt.total_seconds() / 60
    st["nrei"] = [reiteradas(a, i, f) for a, i, f in zip(st["abonado_codigo"], st["id_os"], st["fr"])]
    fi = pd.to_datetime(st["fecha_instalacion_abonado"], errors="coerce")
    st["dinf"] = (st["fr"] - fi).dt.days
    st["inf"] = (st["dinf"] >= 0) & (st["dinf"] <= 30)

    def clasificar(r) -> str:
        if _txt(r["agenda_estado"]) == "ok":   # solo el formato #AGENDA ATC (o MCD) dd/mm hh-hh# es agenda (no corre el contador)
            return "agenda"
        if _RE_VISITA.search(_txt(r.get("observaciones"))):   # 1.ª / 2.ª visita o VD en el comentario
            return "agenda"
        if r.get("obs_agenda") is True or _txt(r.get("obs_agenda")).lower() == "true":
            # Igual que el tablero de pendientes: el comentario menciona «agenda», «primera visita» o «segunda visita»
            # (ej. «SE AGENDA VISITA TECNICA», «#PRIMERA VISTA 12/10#»): no entra al cálculo de los 100 min.
            return "agenda"
        if pd.notna(r["rm"]) and r["rm"] > 19 * 60:
            return "tarde"
        if pd.notna(r["rm"]) and r["rm"] < 8 * 60:
            return "temprano"
        franja = bool(_RE_FRANJA.search(_txt(r["horario_atencion"])))
        if pd.isna(r["mins"]) or r["mins"] < 0:
            if r["estado_os"] == "REGISTRADA":
                if pd.isna(r["start"]):
                    return "sin"
                transcurrido = (corte - r["start"]).total_seconds() / 60
                return "curso" if transcurrido <= SLA else ("fexcl" if franja else "no")
            return "sin"
        if r["mins"] <= SLA:
            return "si"
        return "fexcl" if franja else "no"

    del_mes = lambda s: (s.dt.year == anio) & (s.dt.month == mes)  # noqa: E731
    A = st[st["tipo"] == "AVERIA"]

    L = st[(st["estado_os"] == "DESCARGADA") & del_mes(st["fa"])]
    liq = [{
        "d": int(r["fa"].day), "ec": _contrata(r["contrata"]) or "SIN EECC", "di": _txt(r["distrito"]) or "SIN DISTRITO",
        "m": (int(r["tmi"]) if pd.notna(r["tmi"]) and r["tmi"] >= 0 else None), "rei": bool(r["nrei"]), "inf": bool(r["inf"]),
        "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "tec": _txt(r["tecnico_ejecutor"]), "rec": _iso(r["start"]),
        "hi": r["hi"].strftime("%H:%M") if pd.notna(r["hi"]) else "", "hf": r["hf"].strftime("%H:%M") if pd.notna(r["hf"]) else "",
        "fat": r["fa"].strftime("%Y-%m-%d"), "fr": _txt(r["horario_atencion"]), "ca": _txt(r["causa"]),
        "sca": _txt(r["sub_causa"]), "sol": _txt(r["solucion"]), "cn": _canal(r.get("canal_atencion")),
        "t": r["tipo"], "ts": r["ts"], "tmr": (round(r["tmr"]) if pd.notna(r["tmr"]) and r["tmr"] >= 0 else None),
    } for _, r in L.iterrows()]

    # 100 min: solo las combinaciones de MOTIVOS_100 (RED CLIENTE · INTERNET, y PROBLEMA TV también con SERVICIO OTT)
    R = A[del_mes(A["fr"]) & (A["estado_os"] != "ANULADA")
          & pd.Series([entra_100(m, l, s) for m, l, s in zip(A["motivo"], A["lugar_averia"], A["servicio"] if "servicio" in A else [""] * len(A))],
                      index=A.index, dtype=bool)]
    cien = [{
        "d": int(r["fr"].day), "k": clasificar(r), "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "di": _txt(r["distrito"]),
        "ec": _contrata(r["contrata"]) or "POR ASIGNAR", "tec": _txt(r["tecnico_ejecutor"]) or _txt(r["tecnico_responsable"]),
        "rec": _iso(r["start"]), "ini": _iso(r["end"]), "min": (round(r["mins"]) if pd.notna(r["mins"]) and r["mins"] >= 0 else None),
        "fr": _txt(r["horario_atencion"]), "e": r["estado_os"], "cn": _canal(r.get("canal_atencion")),
    } for _, r in R.iterrows()]

    ing = [{"d": int(r["fr"].day), "di": _txt(r["distrito"]) or "SIN DISTRITO", "t": r["tipo"], "ts": r["ts"],
            "cn": _canal(r.get("canal_atencion"))} for _, r in st[del_mes(st["fr"])].iterrows()]

    pend = []
    for _, r in st[st["estado_os"] == "REGISTRADA"].sort_values("start").iterrows():
        tec = _txt(r["tecnico_responsable"])
        pend.append({
            "z": _zona(r["distrito"]), "di": _txt(r["distrito"]), "seg": _txt(r["segmento"]), "ab": int(r["abonado_codigo"]),
            "os": int(r["nro_os"]), "t": r["tipo"], "ts": r["ts"], "inf": bool(r["inf"]), "infd": (int(r["dinf"]) if pd.notna(r["dinf"]) else None),
            "rei": int(r["nrei"]), "ec": (_contrata(tec_contrata.get(tec)) if tec else None) or "POR ASIGNAR", "tec": tec,
            "rec": _iso(r["start"]), "fr": _txt(r["horario_atencion"]), "ags": _txt(r["agenda_estado"]) or "vacio",
            "ag": _txt(r["agenda_texto"]), "ini": _iso(r["end"]), "mot": _txt(r["motivo"]),
            "fi": _txt(r["fecha_instalacion_abonado"])[:10] or None,
            "inst": instalacion(r["abonado_codigo"], r["fr"]),
            "prev": anteriores(r["abonado_codigo"], r["id_os"], r["fr"]) if r["nrei"] else [],
            "pl": _txt(r.get("plano")), "viv": _txt(r.get("tipo_vivienda")), "plan": _txt(r.get("tarifa_servicio")),
            "zf": _txt(r.get("zona_fftt")), "nap": _txt(r.get("nap")), "bor": _txt(r.get("borne")), "dis": _txt(r.get("dispositivo")),
            "ca": _txt(r["causa"]), "sol": _txt(r["solucion"]), "cn": _canal(r.get("canal_atencion")),
            "obs": _txt(r.get("observaciones")), "ovt": _txt(r.get("obs_visita_todo")), "ove": _txt(r.get("obs_visita_en")),
            "co": _txt(r.get("coordenadas")), "ecp": _eecc_plano(r.get("distrito"), r.get("plano")),
        })

    # Agendas de averías y TC (mismo criterio que instalaciones): si la OS trae franja en HORARIO ATENCION OS con su
    # Fecha Asignacion, se respeta; si no, el día y franja de #AGENDA ATC o MCD dd/mm h1-h2# de las observaciones. Solo las del mes.
    MES_N = {m: i + 1 for i, m in enumerate(["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"])}
    _RE_AGT = re.compile(r"(\d{1,2})-([a-z]{3})\.\s*(\d{1,2})\s*-\s*(\d{1,2})")
    fpg = pd.to_datetime(st["fecha_programada"], errors="coerce") if "fecha_programada" in st else pd.Series(pd.NaT, index=st.index)

    def agenda_de(r, fp) -> tuple:
        mf = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})", _txt(r["horario_atencion"]))
        if mf and pd.notna(fp):  # la OS trae franja (con su Fecha Asignacion): se respeta
            return fp.normalize(), int(mf[1]), int(mf[2]), "os"
        m = _RE_AGT.search(_txt(r["agenda_texto"]).lower())
        if m and m[2] in MES_N:
            mm = MES_N[m[2]]
            yy = anio - 1 if mm == 12 and mes == 1 else anio + 1 if mm == 1 and mes == 12 else anio
            try:
                return pd.Timestamp(yy, mm, int(m[1])), int(m[3]), int(m[4]), "obs"
            except ValueError:
                pass
        return None

    def cumple(fecha, h2, hi) -> str:
        if pd.notna(hi):
            if hi.normalize() != fecha:
                return "no" if hi.normalize() > fecha else "si"
            return "tarde" if hi.hour * 60 + hi.minute > h2 * 60 else "si"   # 10:00 en 8 - 10 aún cumple
        hoy = corte.normalize()
        return "no" if fecha < hoy else "hoy" if fecha == hoy else "fut"

    agd = []
    for i, r in st[st["estado_os"] != "ANULADA"].iterrows():
        a = agenda_de(r, fpg.get(i))
        if not a or a[0].year != anio or a[0].month != mes:
            continue
        fecha, h1, h2, fo = a
        tec = _txt(r["tecnico_ejecutor"]) or _txt(r["tecnico_responsable"])
        agd.append({"d": int(fecha.day), "fr": f"{h1} - {h2}", "t": r["tipo"], "ts": r["ts"], "fo": fo, "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]),
                    "di": _txt(r["distrito"]), "z": _zona(r["distrito"]), "seg": _txt(r["segmento"]),
                    "ec": _contrata(r["contrata"]) or (_contrata(tec_contrata.get(tec)) if tec else None) or "POR ASIGNAR", "tec": tec,
                    "rec": _iso(r["start"]), "ini": _iso(r["end"]), "e": {"DESCARGADA": "D", "REGISTRADA": "R", "OBSERVADA": "O"}.get(r["estado_os"], "R"),
                    "k": cumple(fecha, h2, r["hi"]), "cn": _canal(r.get("canal_atencion"))})

    for lista in (liq, cien, ing):
        for x in lista:
            x["z"] = _zona(x["di"])

    # Altas del mes (instalaciones DESCARGADA por fecha de atención): denominador de % Infancia, igual que el tablero
    # anterior (Ctd Infancia ÷ Total de Alta). Agrupadas por día, zona y canal: [día, zona, canal, cantidad].
    alta = []
    if inst is not None and len(inst):
        ia = inst.assign(fa=pd.to_datetime(inst["fecha_atencion"], errors="coerce")).dropna(subset=["fa"])
        ia = ia[(ia["fa"].dt.year == anio) & (ia["fa"].dt.month == mes)]
        if len(ia):
            g = ia.assign(d=ia["fa"].dt.day, z=[_zona(x) for x in (ia["distrito"] if "distrito" in ia else [""] * len(ia))],
                          cn=[_canal(x) for x in (ia["canal_atencion"] if "canal_atencion" in ia else [""] * len(ia))]).groupby(["d", "z", "cn"]).size()
            alta = [[int(d), z, cn, int(v)] for (d, z, cn), v in g.items()]
    return {"liq": liq, "cien": cien, "ing": ing, "pend": pend, "agd": agd, "alta": alta, "zonas": zonas,
            "corte": corte.strftime("%Y-%m-%dT%H:%M:00"), "anio": anio, "mes": mes, "dias": corte.day}


def inicio_periodo(hoy: date) -> date:
    primero = hoy.replace(day=1)
    return (primero - timedelta(days=1)).replace(day=1)
