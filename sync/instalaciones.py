"""Calcula el tablero de Instalaciones y lo guarda en `tableros` (clave "instalaciones:FILIAL").

Universo: órdenes INSTALACION SERVICIO del servicio INTERNET (la OS del servicio OTT que nace con la
misma venta se descarta para no contar dos veces al mismo abonado).

Listas que dibuja public/instalaciones.html:
  ing   ingresos del mes por día de recepción (con su estado actual)
  liq   instalaciones liquidadas (DESCARGADA) en el mes por fecha de atención
  pend  pendientes (REGISTRADA) con su agenda
  prog  agendas (franja elegida) y coordinadas (sin franja) programadas en el mes, con su cumplimiento
  pdia  pendientes al cierre de cada día del mes
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from .averias import TZ, _canal, _contrata, _iso, _lima, _txt, inicio_periodo  # noqa: F401  (inicio_periodo se reexporta)
from .zonas import ZONAS

COLUMNAS_BD = (
    "id_os,nro_os,estado_os,abonado_codigo,distrito,segmento,categoria,servicio,plano,tipo_vivienda,fecha_registro,hora_recepcion,"
    "fecha_atencion,fecha_programada,fecha_cita,atencion_inicio,atencion_fin,horario_atencion,tecnico_ejecutor,tecnico_responsable,contrata,"
    "tarifa_servicio,cajas_cerca,obs_visita,obs_visita_en,coordenadas,visita_agenda,inicio_fachada,obs_visita_todo,canal_atencion,observaciones,"
    "motivo_observacion,submotivo_observacion,fecha_observacion,usuario_observacion"
)

# 1.ª / 2.ª visita o VD (visita directa) mencionada en el comentario; se toma la última mención.
_RE_VISITA = re.compile(r"(?:(PRIMERA|SEGUNDA|[12])\s*(?:ERA|RA|ER|DA|DO|°|º|\.ª|ª)?\s*VISI?T[AO]S?\b|(VISITA\s+DIRECTA|\bVD)\b)", re.I)


def _seg(r) -> str:
    """Segmento que se muestra (SEG. y filtro Segmento): REF si la Categoría es REFERIDOS; si no, el Segmento (B2C, B2B…)."""
    if _txt(r.get("categoria")).strip().upper() == "REFERIDOS":
        return "REF"
    return _txt(r.get("segmento"))


def _visita(*textos) -> str | None:
    m = list(_RE_VISITA.finditer(" ".join(_txt(t) for t in textos)))
    if not m:
        return None
    m = m[-1]
    if m[2]:
        return "VD"
    return "2" if m[1].upper() in ("SEGUNDA", "2") else "1"


_RE_TEC_COMENTARIO = re.compile(r"%([^%]{3,80})%")


def _tec_comentario(*textos) -> str:
    """Último técnico escrito entre %…% en los comentarios (OBS VISITA TÉCNICO u observaciones)."""
    for t in textos:
        m = _RE_TEC_COMENTARIO.findall(_txt(t))
        if m:
            return m[-1].strip()
    return ""


# Valores que el área de operaciones ajusta (no vienen de ScordSoft).
CONFIG = {
    "meta_mes": {"LIMA": 5500},          # instalaciones a liquidar en el mes
    "meta_conv": 90,                     # % convertibilidad
    "meta_disp": 13,                     # % dispersión (pendientes con 3 días o más)
    "meta_cumpl": 95,                    # % cumplimiento de agendas y coordinadas
    "obj_tecnico": 3,                    # instalaciones por técnico al día
    "meta_db": 1.5,                      # días de bolsa
    "meta_prod": 2.5,                    # productividad (inst. por técnico al día)
    # Despachadores: sus AB toman el técnico que figura entre %…% en la última observación del técnico
    # y la EECC de ese técnico según la base.
    "despachadores": ["VERGARA PADILLA, CESAR"],
    # Planta según el código del plano (igual que en pendientes-op-lima).
    "plantas": [["^PA-", "PANGEA"], [r"^LI\.", "FIBERLUX"], [r"^P\d", "ECONOCABLE"], ["^SMP", "UFINET"], [r"^VERT\.", "VERTICAL"],
                [r"^[A-ZÑ ]+([ -]T\d+)?$", "ATP"]],
}
_RE_FRANJA = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})")


def _norm(v) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", _txt(v)).encode("ascii", "ignore").decode().upper().strip()


def _cargar_planos() -> tuple[dict, dict]:
    """EECC que le corresponde a cada plano (hoja ZONA de DATOS_TEC): por distrito+plano y, si es única, solo por plano."""
    import json
    from pathlib import Path
    import gzip
    try:
        m = json.loads(gzip.decompress((Path(__file__).with_name("planos_eecc.json.gz")).read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        return {}, {}
    por_plano: dict[str, set] = {}
    for k, ec in m.items():
        por_plano.setdefault(k.split("|", 1)[1], set()).add(ec)
    return m, {p: next(iter(e)) for p, e in por_plano.items() if len(e) == 1}


PLANOS_EECC, PLANO_UNICO = _cargar_planos()


def eecc_del_plano(distrito, plano) -> str | None:
    d, p = _norm(distrito), _norm(plano)
    if not p:
        return None
    return PLANOS_EECC.get(f"{d}|{p}") or PLANO_UNICO.get(p)


def _planta(plano, reglas) -> str:
    p = _txt(plano).strip().upper()
    if not p:
        return "Sin plano"
    for patron, nombre in reglas:
        if re.search(patron, p):
            return nombre
    return "Otros planos"


def _lista(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        import json
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def construir(df: pd.DataFrame, tec_contrata: dict[str, str], corte: datetime, filial: str = "LIMA",
              zonas: dict[str, str] = ZONAS, config: dict = CONFIG, solo_internet: bool = True) -> dict:
    """df: órdenes INSTALACION SERVICIO desde el inicio del mes anterior, con las columnas de la BD."""
    _zona = lambda d: zonas.get(_txt(d).upper().strip(), "SIN ZONA")  # noqa: E731
    corte = pd.Timestamp(corte).tz_localize(None) if pd.Timestamp(corte).tzinfo else pd.Timestamp(corte)
    anio, mes, hoy = corte.year, corte.month, corte.normalize()

    df = df.copy()
    if solo_internet and "servicio" in df and df["servicio"].notna().any():
        df = df[df["servicio"].fillna("INTERNET") == "INTERNET"]
    reg = _lima(df["fecha_registro"])
    df["fr"] = reg.dt.normalize()
    df["rec"] = reg.where(df["hora_recepcion"].notna() & (df["hora_recepcion"].astype(str) != ""))
    df["fa"] = pd.to_datetime(df["fecha_atencion"], errors="coerce")
    df["fp"] = pd.to_datetime(df.get("fecha_programada"), errors="coerce")  # Fecha Asignacion: día de la ruta del técnico
    df["fc"] = pd.to_datetime(df.get("fecha_cita"), errors="coerce")        # Fecha Atencion: cita ofrecida al cliente en la venta
    df["hi"] = _lima(df["atencion_inicio"])
    df["hf"] = _lima(df["atencion_fin"])
    df["ifa"] = _lima(df["inicio_fachada"]) if "inicio_fachada" in df else pd.NaT  # FechaInicioFachada
    df["pl"] = [_planta(p, config["plantas"]) for p in df.get("plano", pd.Series(index=df.index, dtype=object))]
    df["ec"] = [_contrata(c) or (_contrata(tec_contrata.get(_txt(t))) if _txt(t) else None)
                for c, t in zip(df["contrata"], df["tecnico_responsable"])]
    df["tec"] = [_txt(a) or _txt(b) for a, b in zip(df["tecnico_ejecutor"], df["tecnico_responsable"])]
    # Agenda o coordinada (criterio de operaciones):
    #  1) Fecha y franja: si la OS trae franja en HORARIO ATENCION OS (con su Fecha Asignacion), se respeta la de la OS;
    #     solo si la OS no tiene franja se busca en el comentario más reciente del técnico (OBS VISITA TÉCNICO) día y franja.
    #  2) Con esa fecha y franja: si el inicio de la franja queda a más de 24 h de la F. Recep. es AGENDA; si queda a
    #     24 h o menos es COORDINADA.
    #  3) En otro caso (sin franja en la OS ni agenda en el último comentario): SIN AGENDA ("SA").
    def programa(r) -> tuple[str, object, str, str]:
        fr_os, fp = _txt(r["horario_atencion"]), r["fp"]
        tipo = lambda dia, h1: "AG" if pd.isna(r["rec"]) or dia + pd.Timedelta(hours=h1) - r["rec"] > pd.Timedelta(hours=24) else "CO"  # noqa: E731
        m = _RE_FRANJA.search(fr_os)
        if pd.notna(fp) and m:  # la OS trae franja: se respeta
            return tipo(fp, int(m[1])), fp, f"{int(m[1])} - {int(m[2])}", "os"
        va = _txt(r.get("visita_agenda"))
        if "|" in va:  # sin franja en la OS: día y franja del comentario del técnico
            dia, franja = va.split("|", 1)
            f, mf = pd.to_datetime(dia, errors="coerce"), _RE_FRANJA.search(franja)
            if pd.notna(f) and mf:
                return tipo(f.normalize(), int(mf[1])), f.normalize(), franja, "obs"
        return "SA", fp, fr_os, "os"
    prg = [programa(r) for _, r in df.iterrows()]
    # Fecha Asignacion y franja tal como vienen en la OS (el portal pinta en verde la agenda que coincide con ellas).
    df["fpo"] = df["fp"]
    df["fro"] = [(lambda m: f"{int(m[1])} - {int(m[2])}" if m else "")(_RE_FRANJA.search(_txt(v))) for v in df["horario_atencion"]]
    df["tp"] = [x[0] for x in prg]
    df["fp"] = pd.to_datetime(pd.Series([x[1] for x in prg], index=df.index), errors="coerce")
    df["horario_atencion"] = [x[2] for x in prg]
    df["org"] = [x[3] for x in prg]
    del_mes = lambda s: (s.dt.year == anio) & (s.dt.month == mes)  # noqa: E731
    E = {"DESCARGADA": "D", "REGISTRADA": "R", "OBSERVADA": "O", "ANULADA": "A"}
    viv = lambda v: _txt(v).replace("HORIZONTAL ", "H. ") or "—"  # noqa: E731

    def eecc_ingreso(r) -> tuple[str, str]:
        """EECC del ingreso para la convertibilidad. Observadas y anuladas: la del técnico escrito entre %…% en el comentario."""
        tec, ec = r["tec"], _txt(r["ec"])
        if r["estado_os"] in ("OBSERVADA", "ANULADA"):
            t = _tec_comentario(r.get("obs_visita_todo"), r.get("obs_visita"), r.get("observaciones"))
            if t:
                tec, ec = t, _contrata(tec_contrata.get(t)) or ec
        return tec, ec or "POR ASIGNAR"

    ing = [{"d": int(r["fr"].day), "di": _txt(r["distrito"]) or "SIN DISTRITO", "seg": _seg(r),
            "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "rec": _iso(r["rec"]),
            "e": E.get(r["estado_os"], "R"), "pl": r["pl"], "ec": eecc_ingreso(r)[1], "tec": eecc_ingreso(r)[0],
            "cn": _canal(r.get("canal_atencion"))} for _, r in df[del_mes(df["fr"])].iterrows()]

    L = df[(df["estado_os"] == "DESCARGADA") & del_mes(df["fa"])]
    liq = [{
        "d": int(r["fa"].day), "ec": _txt(r["ec"]) or "SIN EECC", "tec": r["tec"], "di": _txt(r["distrito"]) or "SIN DISTRITO",
        "seg": _seg(r), "pl": r["pl"], "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "rec": _iso(r["rec"]),
        "hi": r["hi"].strftime("%H:%M") if pd.notna(r["hi"]) else "", "hf": r["hf"].strftime("%H:%M") if pd.notna(r["hf"]) else "",
        "fr": _txt(r["horario_atencion"]), "t": r["tp"], "fo": r["org"],
        "fp": r["fp"].strftime("%Y-%m-%d") if pd.notna(r["fp"]) else None, "cn": _canal(r.get("canal_atencion")),
        "fpo": r["fpo"].strftime("%Y-%m-%d") if pd.notna(r["fpo"]) else None, "fro": r["fro"], 
    } for _, r in L.iterrows()]

    despach = [d.upper() for d in config.get("despachadores", [])]

    def tecnico_pend(r) -> tuple[str, str]:
        """Técnico y EECC del pendiente; si lo tiene un despachador, se usa el técnico de la última observación."""
        tec, ec = _txt(r["tecnico_responsable"]), _txt(r["ec"]) or "POR ASIGNAR"
        if tec and any(d in tec.upper() for d in despach):
            otro = _txt(r.get("obs_visita_en")).split(" · ")[-1].strip()
            if otro and not re.match(r"^\d{2}/\d{2}/\d{4}", otro):
                tec = otro
                ec = _contrata(tec_contrata.get(otro)) or ec
        return tec, ec

    def gestionado(r) -> bool:
        """Gestionado = la OS tiene comentario en OBS VISITA TÉCNICO; si no tiene, es Sin gestión."""
        return bool(_txt(r.get("obs_visita_todo")).strip() or _txt(r.get("obs_visita")).strip())

    pend = [{
        "di": _txt(r["distrito"]), "seg": _seg(r), "viv": viv(r.get("tipo_vivienda")), "pl": r["pl"],
        "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "ec": tecnico_pend(r)[1], "tec": tecnico_pend(r)[0], "ges": gestionado(r),
        "rec": _iso(r["rec"]), "cita": r["fc"].strftime("%Y-%m-%d") if pd.notna(r["fc"]) else None, "fp": r["fp"].strftime("%Y-%m-%d") if pd.notna(r["fp"]) else None, "fr": _txt(r["horario_atencion"]),
        "plano": _txt(r.get("plano")), "plan": _txt(r.get("tarifa_servicio")), "cj": _lista(r.get("cajas_cerca")),
        "ov": _txt(r.get("obs_visita")), "ove": _txt(r.get("obs_visita_en")), "ovt": _txt(r.get("obs_visita_todo")), "co": _txt(r.get("coordenadas")),
        "t": r["tp"], "fo": r["org"], "ini": _iso(r["ifa"] if pd.notna(r["ifa"]) else r["hi"]), "ecp": eecc_del_plano(r["distrito"], r.get("plano")),
        "cn": _canal(r.get("canal_atencion")), "to": _txt(r.get("tipo_orden")) or "INSTALACION SERVICIO",
        "vi": _visita(r.get("observaciones"), r.get("obs_visita_todo") or r.get("obs_visita")), "fpo": r["fpo"].strftime("%Y-%m-%d") if pd.notna(r["fpo"]) else None, "fro": r["fro"], 
    } for _, r in df[df["estado_os"] == "REGISTRADA"].sort_values("rec").iterrows()]

    # Agendas (cliente eligió franja) y coordinadas (sin franja: la fecha la coordina el gestor), por fecha programada.
    def cumplimiento(r) -> str:
        fp = r["fp"]
        if pd.notna(r["hi"]):
            if r["hi"].normalize() != fp:
                return "no" if r["hi"].normalize() > fp else "si"   # atendida antes de la fecha también cumple
            m = _RE_FRANJA.search(_txt(r["horario_atencion"]))
            return "tarde" if m and r["hi"].hour * 60 + r["hi"].minute > int(m[2]) * 60 else "si"  # 10:00 en 8 - 10 aún cumple
        if fp < hoy:
            return "no"
        return "hoy" if fp == hoy else "fut"

    P = df[del_mes(df["fp"])]  # todas las del mes por F. AG (el portal separa sin agenda y anuladas; permite editarlas)
    prog = [{
        "d": int(r["fp"].day), "t": r["tp"], "fo": r["org"], "pl": r["pl"],
        "fr": _txt(r["horario_atencion"]), "ec": _txt(r["ec"]) or "POR ASIGNAR", "tec": r["tec"], "di": _txt(r["distrito"]),
        "seg": _seg(r), "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "e": E.get(r["estado_os"], "R"),
        "rec": _iso(r["rec"]), "ini": _iso(r["ifa"] if pd.notna(r["ifa"]) else r["hi"]), "k": cumplimiento(r),   # inicio: fachada (o atención)
        "cita": r["fc"].strftime("%Y-%m-%d") if pd.notna(r["fc"]) else None, "cn": _canal(r.get("canal_atencion")),
        "fpo": r["fpo"].strftime("%Y-%m-%d") if pd.notna(r["fpo"]) else None, "fro": r["fro"], 
    } for _, r in P.iterrows()]

    # Pendientes al cierre de cada día: recibidas hasta ese día y aún sin atender ese día (no cuenta anuladas).
    vivas = df[df["estado_os"].isin(["REGISTRADA", "DESCARGADA"])]
    pdia = {}
    for d in range(1, corte.day + 1):
        dia = pd.Timestamp(anio, mes, d)
        pdia[d] = int(((vivas["fr"] <= dia) & ((vivas["estado_os"] == "REGISTRADA") | (vivas["fa"] > dia))).sum())

    for lista in (ing, liq, pend, prog):
        for x in lista:
            x["z"] = _zona(x["di"])
    # Observadas (página Observadas): OS que siguen en estado OBSERVADA, por fecha de observación (hora de Lima).
    ob = df[df["estado_os"] == "OBSERVADA"].copy()
    ob["fo"] = _lima(ob["fecha_observacion"]) if "fecha_observacion" in ob else pd.NaT
    ob = ob[ob["fo"].notna()]
    obs = [{
        "f": r["fo"].strftime("%Y-%m-%d"), "h": r["fo"].strftime("%H:%M"), "m": _txt(r.get("motivo_observacion")).strip() or "SIN CATEGORÍA",
        "s": _txt(r.get("submotivo_observacion")).strip() or "SIN MOTIVO REGISTRADO", "u": _txt(r.get("usuario_observacion")),
        "ab": int(r["abonado_codigo"]), "os": int(r["nro_os"]), "rec": _iso(r["rec"]), "ec": eecc_ingreso(r)[1], "tec": eecc_ingreso(r)[0],
        "di": _txt(r["distrito"]) or "SIN DISTRITO", "z": _zona(r["distrito"]), "pl": r["pl"], "seg": _seg(r), "cn": _canal(r.get("canal_atencion")),
        "co": _txt(r.get("coordenadas")), "plan": _txt(r.get("tarifa_servicio")), "ovt": (_txt(r.get("obs_visita_todo")) or _txt(r.get("obs_visita")))[:900],
    } for _, r in ob.iterrows()]

    return {"ing": ing, "liq": liq, "pend": pend, "prog": prog, "pdia": pdia, "zonas": zonas, "obs": obs,
            "config": {**{k: v for k, v in config.items() if k not in ("meta_mes", "plantas")},
                       "meta_mes": config["meta_mes"].get(filial)},
            "corte": corte.strftime("%Y-%m-%dT%H:%M:00"), "anio": anio, "mes": mes, "dias": corte.day}


# ---------- Pendientes de otros tipos de trabajo (sin averías ni TC) ----------
TIPOS_EXCLUIDOS_PEND = ("INSTALACION SERVICIO", "SERVICIO TECNICO")


def pendientes_otros(df: pd.DataFrame, tec_contrata: dict[str, str], corte: datetime, filial: str = "LIMA",
                     zonas: dict[str, str] = ZONAS, excluir: tuple = TIPOS_EXCLUIDOS_PEND) -> list[dict]:
    """Órdenes REGISTRADAS de los demás tipos de orden (traslados, cambio de equipo, recojo, otros…), con los mismos
    campos que los pendientes de instalación. No incluye SERVICIO TECNICO (averías y TC)."""
    if df is None or df.empty:
        return []
    df = df[~df["tipo_orden"].isin(excluir) & (df["estado_os"] == "REGISTRADA")]
    if df.empty:
        return []
    return construir(df, tec_contrata, corte, filial=filial, zonas=zonas, solo_internet=False)["pend"]


def pendientes_otros_averias(df: pd.DataFrame, tec_contrata: dict[str, str], corte: datetime, filial: str = "LIMA",
                             zonas: dict[str, str] = ZONAS) -> list[dict]:
    """Para el tablero de Averías: órdenes REGISTRADAS de los demás tipos de orden, sin INSTALACION SERVICIO y sin las
    averías / TC (que ya son el tablero). Las de SERVICIO TECNICO sin detalle AVERIA/TC salen como su propio tipo."""
    if df is None or df.empty:
        return []
    df = df.copy()
    det = df["detalle"].fillna("") if "detalle" in df else pd.Series("", index=df.index)
    st = df["tipo_orden"] == "SERVICIO TECNICO"
    df = df[~(st & det.isin(["AVERIA", "TC"]))]
    df.loc[df["tipo_orden"] == "SERVICIO TECNICO", "tipo_orden"] = "SERVICIO TECNICO (SIN DETALLE)"
    return pendientes_otros(df.drop(columns=["detalle"], errors="ignore"), tec_contrata, corte, filial=filial, zonas=zonas,
                            excluir=("INSTALACION SERVICIO",))


# ---------- Avance total: todas las órdenes DESCARGADAS del mes hechas por técnicos de EECC ----------
COLUMNAS_AVT = "id_os,filial,tipo_orden,servicio,tecnico_ejecutor,contrata,fecha_atencion,nro_os,abonado_codigo,atencion_inicio,atencion_fin,distrito"


def avance_total(df: pd.DataFrame, anio: int, mes: int) -> dict:
    """Órdenes DESCARGADAS del mes (F. Atenc.) con contrata EECC, una fila por orden en forma compacta:
    tablas de nombres (t tipos, s servicios, e EECC, k técnicos, di distritos) y filas
    [día, tipo, servicio, eecc, técnico, nro_os, abonado, hora_i, hora_f, distrito] con índices a esas tablas.
    Las órdenes de OFICINA / áreas internas no entran."""
    vacio = {"t": [], "s": [], "e": [], "k": [], "di": [], "o": []}
    if df is None or df.empty:
        return vacio
    x = df.assign(fa=pd.to_datetime(df["fecha_atencion"], errors="coerce"))
    x = x[(x["fa"].dt.year == anio) & (x["fa"].dt.month == mes)]
    if x.empty:
        return vacio
    hora = lambda v: (lambda t: t.strftime("%H:%M") if pd.notna(t) else "")(pd.to_datetime(v, errors="coerce"))  # noqa: E731
    x = x.assign(d=x["fa"].dt.day, to=x["tipo_orden"].map(_txt).replace("", "SIN TIPO"), sv=x["servicio"].map(_txt).replace("", "SIN SERVICIO"),
                 ec=[_contrata(c) or "SIN EECC" for c in x["contrata"]], tec=x["tecnico_ejecutor"].map(_txt).replace("", "SIN TÉCNICO"),
                 dis=x["distrito"].map(_txt))
    tablas = {k: sorted(x[c].unique().tolist()) for k, c in (("t", "to"), ("s", "sv"), ("e", "ec"), ("k", "tec"), ("di", "dis"))}
    idx = {k: {v: i for i, v in enumerate(vs)} for k, vs in tablas.items()}
    ent = lambda v: int(v) if pd.notna(v) and str(v).strip() not in ("", "nan") else None  # noqa: E731
    filas = [[int(r.d), idx["t"][r.to], idx["s"][r.sv], idx["e"][r.ec], idx["k"][r.tec], ent(r.nro_os), ent(r.abonado_codigo),
              hora(r.atencion_inicio), hora(r.atencion_fin), idx["di"][r.dis]] for r in x.sort_values(["fa", "atencion_inicio"]).itertuples()]
    return {**tablas, "o": filas}
