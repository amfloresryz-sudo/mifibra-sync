"""Convierte el Excel de ScordSoft en filas limpias para la tabla `ordenes_servicio`.

Solo se conservan las columnas de la lista blanca COLUMNAS. Datos personales del
abonado (nombre, documento, dirección, teléfono, observaciones libres, deuda) nunca
salen de este paso. Excepción pedida por operaciones: las coordenadas (Latitud/Longitud
MAPS) de las altas, para ubicar el punto en la ficha del AB.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

FILA_ENCABEZADO = 5  # El Excel trae 5 filas de título antes de los encabezados.

# columna en ScordSoft -> columna en la base de datos
COLUMNAS = {
    "IdOS": "id_os",
    "Nro OS": "nro_os",
    "Filial": "filial",
    "Tipo Orden": "tipo_orden",
    "Estado Os": "estado_os",
    "Sub Estado": "sub_estado",
    "Abonado": "abonado_codigo",
    "Categoría": "categoria",
    "Tarifa Servicio": "tarifa_servicio",
    "Distrito": "distrito",
    "Fecha Ejecucion": "fecha_ejecucion",
    "HORARIO ATENCION OS": "horario_atencion",
    "Nombres Tecnico Ejecutor": "tecnico_ejecutor",
    "AREA TECNICO EJECUTOR": "contrata",
    "MOTIVO": "motivo",
    "LUGAR AVERIA": "lugar_averia",
    "Segmento": "segmento",
    "Nombres Tecnico Responsable": "tecnico_responsable",
    "CAUSA": "causa",
    "SUB CAUSA": "sub_causa",
    "SOLUCION": "solucion",
    "motivo_observacion": "motivo_observacion",
    "submotivo_observacion": "submotivo_observacion",
    "usuario_observacion": "usuario_observacion",
    "Mot. Anulación": "motivo_anulacion",
    "Usuario Mod": "usuario_mod",
    "CANAL DE ATENCIÓN": "canal_atencion",
    "Servicio": "servicio",
    "Plano": "plano",
    "Tipo Vivienda": "tipo_vivienda",
    "Zona..": "zona_fftt",
    "NAP": "nap",
    "BORNE": "borne",
    "Dispositivo": "dispositivo",
}

# Columnas de fecha + hora que se combinan en un solo timestamp.
FECHA_HORA = {
    # destino: (columna fecha, columna hora, ¿sin hora no vale?)
    "fecha_registro": ("F. Recep.", "Hora", False),
    "atencion_inicio": ("F. Atenc.", "Hora I", True),
    "atencion_fin": ("F. Atenc.", "Hora F", True),
    "fecha_mod": ("Fecha Mod", "Hora Mod", False),
}

ENTEROS = {"id_os", "nro_os", "abonado_codigo"}

# Columnas solo de fecha (sin hora).
FECHAS = {"F. Atenc.": "fecha_atencion", "Fecha Instalación Abonado": "fecha_instalacion_abonado", "Fecha Asignacion": "fecha_programada", "Fecha Atencion": "fecha_cita"}

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_RE_AGENDA = re.compile(r"#\s*AGENDA(?:\s+ATC)?\s+(\d{1,2})/(\d{1,2})\s+(\d{1,2})\s*-\s*(\d{1,2})\s*#", re.I)
_RE_VISITA = re.compile(r"agenda|(?:primera|segunda)\s*vis", re.I)
_RE_HORA = re.compile(r"^(\d{1,2}):(\d{2})")
_RE_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


class ErrorFormato(ValueError):
    """El Excel no tiene la forma esperada (ScordSoft cambió el reporte)."""


def _texto(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _ts(v) -> pd.Timestamp:
    """Fecha de ScordSoft: celda de fecha de Excel o texto 'dd/mm/aaaa' (día primero, nunca mes primero)."""
    if isinstance(v, str):
        m = _RE_DMY.match(v.strip())
        if m:  # dd/mm/aaaa [hh:mm[:ss]]
            d, mes, a, h, mi, se = m.groups()
            return pd.Timestamp(int(a), int(mes), int(d), int(h or 0), int(mi or 0), int(se or 0))
    return pd.Timestamp(v)


def _combinar(fecha, hora) -> str | None:
    """Une una fecha y una hora ('15:59' o '15:59:00') en ISO 8601 con zona de Lima."""
    if fecha is None or pd.isna(fecha):
        return None
    f = _ts(fecha)
    h = _texto(hora)
    if h:
        partes = str(h).split(":")
        try:
            f = f.replace(hour=int(partes[0]), minute=int(partes[1]), second=int(partes[2]) if len(partes) > 2 else 0)
        except (ValueError, IndexError):
            pass
    return f.strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"


def _fecha_obs(v) -> str | None:
    """fecha_observacion viene como texto 'dd/mm/aaaa hh:mm'."""
    v = _texto(v)
    if not v:
        return None
    try:
        return datetime.strptime(str(v), "%d/%m/%Y %H:%M").strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"
    except ValueError:
        return None


def _fecha(v) -> str | None:
    v = _texto(v)
    if v is None:
        return None
    try:
        return _ts(v).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _agenda(obs) -> dict:
    """Las observaciones son texto libre y NO se guardan: solo se extrae la agenda y un indicador.

    - obs_agenda: menciona agenda, primera visita o segunda visita (se excluye de la meta de 100 min).
    - agenda_estado: ok (#AGENDA ATC dd/mm h1-h2#), sinformato (hay texto sin ese formato) o vacio.
    - agenda_texto: la agenda ya formateada, p. ej. "15-oct. 8 - 10".
    """
    t = str(_texto(obs) or "")
    m = _RE_AGENDA.search(t)
    texto = None
    if m and 1 <= int(m[2]) <= 12:
        texto = f"{int(m[1]):02d}-{MESES[int(m[2]) - 1]}. {int(m[3])} - {int(m[4])}"
    return {
        "obs_agenda": bool(_RE_VISITA.search(t)),
        "agenda_estado": "ok" if texto else ("vacio" if not t.strip() else "sinformato"),
        "agenda_texto": texto,
    }


def _hora(v) -> str | None:
    m = _RE_HORA.match(str(_texto(v) or "").strip())
    return f"{int(m[1]):02d}:{m[2]}" if m else None


_RE_VISITA_TEC = re.compile(r"#\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*(?:(\d{1,2}):?(\d{2})\d?)?\s*(?:#|%(?=\s))?\s*(?:%([^%]*)%\s*)?(.*?)\s*(?:%([^%]*)%|(?=#\s*\d{1,2}/)|$)", re.S)
_RE_TEC_SUELTO = re.compile(r"%([^%]*)%")
_RE_TELEFONO = re.compile(r"\+?(?=(?:[\s-]?\d){7})\d{1,3}(?:[\s-]?\d{3,})+")  # 7+ dígitos en grupos de 3+ (no confunde "15-10 14")
_RE_URL = re.compile(r"https?://\S+", re.I)


def _sin_tel(t: str) -> str:
    """Oculta los teléfonos (•••) sin tocar los enlaces: los números de un enlace de Speedtest no son teléfonos."""
    partes, ult = [], 0
    for m in _RE_URL.finditer(t):
        partes.append(_RE_TELEFONO.sub("•••", t[ult:m.start()])); partes.append(m.group(0)); ult = m.end()
    partes.append(_RE_TELEFONO.sub("•••", t[ult:]))
    return "".join(partes)


# Agenda dentro de un comentario del técnico: día ("16/10", "HOY", "MAÑANA", "JUEVES") y franja ("8 A 10", "2-4PM", "DE 4 A 6").
_RE_DIA_OBS = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
# "12-10" solo cuenta como fecha si va después de AGENDA / PARA EL / DÍA / FECHA (si no, es una franja: "8-10").
_RE_DIA_GUION = re.compile(r"\b(?:(?:RE)?AGENDA(?:DO|DA)?|COORDINAD[OA]|REPROGRAMA(?:DO|DA)?|PARA|EL|D[IÍ]A|FECHA|LUNES|MARTES|MI[EÉ]RCOLES|JUEVES|VIERNES|S[AÁ]BADO|DOMINGO)\s*:?\s*(\d{1,2})-(\d{1,2})(?:-(\d{2,4}))?(?![\d-])")
_RE_FRANJA_OBS = re.compile(r"(?<![\d/:])(\d{1,2})(?::\d{2})?\s*(AM|PM|A\.M\.?|P\.M\.?)?\s*(?:-|\bA\b|\bA(?=\d)|\bAL\b|\bHASTA\b)\s*(\d{1,2})(?::\d{2})?\s*(AM|PM|A\.M\.?|P\.M\.?)?(?![\d/])")
_RE_HORA_OBS = re.compile(r"(?:\bA\s+LAS\s+|\bHORA\s+|\bDESDE\s+(?:LAS\s+)?)?(?<![\d/:])(\d{1,2})(?::(\d{2}))?\s*(AM|PM|A\.M\.?|P\.M\.?)?(?![\d/])")
_RE_AGENDA_CLAVE = re.compile(r"(?:RE)?AGEND|REPROGRAM|COORDIN|ATENDER[AÁ]|VISITA")
_DIAS_SEM = {"LUNES": 0, "MARTES": 1, "MIERCOLES": 2, "MIÉRCOLES": 2, "JUEVES": 3, "VIERNES": 4, "SABADO": 5, "SÁBADO": 5, "DOMINGO": 6}


def _h24(h: int, ap: str | None) -> int:
    ap = (ap or "").replace(".", "")
    if ap == "PM" and h < 12:
        return h + 12
    if ap == "AM" and h == 12:
        return 12
    if not ap and 1 <= h <= 6:  # "4 A 6" en horario de instalaciones es de la tarde; "7 A 9" es de la mañana
        return h + 12
    return h


def _franja_de_hora(h: int) -> str | None:
    """Hora suelta → franja estándar de 2 h que la contiene (9 → 8 - 10, 16 → 16 - 18)."""
    if not 7 <= h <= 19:
        return None
    ini = min(18, max(8, h - h % 2))
    return f"{ini} - {ini + 2}"


def _agenda_en(texto: str, base: datetime | None) -> str | None:
    """'AAAA-MM-DD|h1 - h2' si el texto trae día y franja; None si falta alguno."""
    t = _RE_TELEFONO.sub(" ", texto.upper())
    fecha = None
    m = _RE_DIA_OBS.search(t) or _RE_DIA_GUION.search(t)
    if m and not (1 <= int(m[2]) <= 12 and 1 <= int(m[1]) <= 31):
        m = None
    if m:
        anio = int(m[3]) if m[3] else (base.year if base else datetime.now().year)
        anio = anio + 2000 if anio < 100 else anio
        if not m[3] and base and int(m[2]) < base.month - 6:
            anio += 1
        try:
            fecha = datetime(anio, int(m[2]), int(m[1]))
        except ValueError:
            fecha = None
        t = t[:m.start(1)] + " " + t[m.end():]
    elif base:
        # Un día de semana explícito manda sobre HOY / MAÑANA ("AGENDA DOMINGO 10 A 12 … HOY TEMPRANO NO PUEDE").
        dm = re.search(r"\b(" + "|".join(_DIAS_SEM) + r")\b(?:\s+(\d{1,2})(?![\d/:-]|\s*(?:-|\bA\b|\bAL\b|AM|PM|:)))?", t)
        if dm and dm[2] and 1 <= int(dm[2]) <= 31:  # "VIERNES 2", "MARTES 20 DE 4 A 6": manda el número del día
            d = int(dm[2])
            mes, anio = (base.month, base.year) if d >= base.day - 15 else ((base.month % 12) + 1, base.year + (base.month == 12))
            try:
                fecha = datetime(anio, mes, d)
            except ValueError:
                fecha = None
        elif dm:
            dias = (_DIAS_SEM[dm[1]] - base.weekday()) % 7
            fecha = base + pd.Timedelta(days=dias or (0 if re.search(r"\bHOY\b", t) else 7))  # "HOY JUEVES" es hoy
        elif re.search(r"PASADO\s+MA[ÑN]ANA", t):
            fecha = base + pd.Timedelta(days=2)
        elif re.search(r"\bMA[ÑN]ANA\b", t):
            fecha = base + pd.Timedelta(days=1)
        elif re.search(r"\bHOY\b", t):
            fecha = base
    if fecha is None:
        return None
    # La franja se busca primero junto a la fecha (hasta la siguiente fecha del texto) y luego en todo el comentario.
    tramo = t
    if m:
        sig = _RE_DIA_OBS.search(t, m.start(1) + 1)
        tramo = t[m.start(1):sig.start() if sig else len(t)]
    agenda = bool(_RE_AGENDA_CLAVE.search(t)) and not re.search(r"\bLLAM[AEO]|CONFIRMAR|COMUNI|COMUNQ|PARA COORDINAR", t)
    fr = _franja_txt(tramo, agenda) or _franja_txt(t, agenda)
    return f"{fecha:%Y-%m-%d}|{fr}" if fr else None


def _franja_txt(t: str, hora_suelta: bool) -> str | None:
    """Franja 'h1 - h2' de un texto: un rango ("8 A 10", "2 A 4 PM") o, si se permite, una hora suelta ("A LAS 9AM")."""
    for f in _RE_FRANJA_OBS.finditer(t):
        # El AM/PM de cada hora manda; si solo la segunda lo trae ("10 A 12 PM", "2 A 4 PM") se aplica a esa.
        h1, h2 = _h24(int(f[1]), f[2]), _h24(int(f[3]), f[4] or f[2])
        if h2 <= h1 < h2 + 12:  # "6 A 8" → 18 - 20
            h2 += 12
        if 7 <= h1 < h2 <= 21 and h2 - h1 <= 4:
            return f"{h1} - {h2}"
    # Sin rango: una hora suelta ("REAGENDA 15/10 A LAS 9AM") → franja estándar que la contiene, solo si el
    # comentario habla de agenda y no es un "llamar a las ..." (eso no es la hora de la visita).
    if hora_suelta:
        for h in _RE_HORA_OBS.finditer(t):
            if not (h[3] or re.match(r"\s*(?:A\s+LAS|HORA|DESDE)", t[h.start():h.start(1)] + " ") or h[2]):
                continue  # un número suelto sin AM/PM, sin ":mm" y sin "A LAS" no es una hora
            fr = _franja_de_hora(_h24(int(h[1]), h[3]))
            if fr:
                return fr
    return None

def _agenda_visita(t: str, base_sin_fecha: datetime | None = None) -> str | None:
    """Agenda (día + franja) del comentario MÁS RECIENTE del técnico. Si ese último comentario no trae día y franja,
    no hay agenda (no se usan comentarios anteriores)."""
    entradas = []
    marcas = list(_RE_VISITA_TEC.finditer(t))
    for m in marcas:
        try:
            base = datetime(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            base = None
        clave = (int(m[3]), int(m[2]), int(m[1]), int(m[4] or 0), int(m[5] or 0))
        entradas.append((clave, m[7], base))
    if not marcas:
        entradas.append(((0, 0, 0, 0, 0), _ultima_sin_fecha(t)[0], base_sin_fecha))
    if not entradas:
        return None
    _, texto, base = max(reversed(entradas), key=lambda x: x[0])  # con la misma fecha y hora, la última escrita
    return _agenda_en(texto or "", base)


def _ultima_sin_fecha(t: str) -> tuple[str, str | None]:
    """Última anotación de un comentario sin fechas: el texto después del último %TÉCNICO% (y ese técnico)."""
    marcas = list(_RE_TEC_SUELTO.finditer(t))
    if not marcas:
        return t.strip(" ,;"), None
    ult = marcas[-1]
    texto = t[ult.end():].strip(" ,;")
    if not texto:  # formato 'texto %TÉCNICO%': el texto va antes de la marca
        previa = marcas[-2].end() if len(marcas) > 1 else 0
        texto = t[previa:ult.start()].strip(" ,;")
    return texto, (ult[1].strip() or None)


def _obs_visita(v, base_sin_fecha: datetime | None = None) -> dict:
    """Observación del técnico más reciente (OBS VISITA TÉCNICO): '#dd/mm/aaaa hh:mm# texto %TÉCNICO%'.
    Se guardan solo la última anotación y su fecha/técnico, con los teléfonos ocultos."""
    t = str(_texto(v) or "").strip()
    if not t:
        return {"obs_visita": None, "obs_visita_en": None, "visita_agenda": None}
    mejor = None
    for m in _RE_VISITA_TEC.finditer(t):
        clave = (int(m[3]), int(m[2]), int(m[1]), int(m[4] or 0), int(m[5] or 0))
        if mejor is None or clave >= mejor[0]:
            mejor = (clave, m)
    if mejor:
        m = mejor[1]
        tec = (m[6] or m[8] or "").strip()
        texto, en = m[7].strip(" ,;"), f"{int(m[1]):02d}/{int(m[2]):02d}/{m[3]}" + (f" {int(m[4]):02d}:{m[5]}" if m[4] else "") + (f" · {tec}" if tec else "")
    else:  # sin fecha: '%TÉCNICO% texto, %TÉCNICO% texto' → la última anotación es la que sigue al último %TÉCNICO%
        texto, en = _ultima_sin_fecha(t)
    return {"obs_visita": _sin_tel(texto)[:600] or None, "obs_visita_en": en, "visita_agenda": _agenda_visita(t, base_sin_fecha)}


def _inicio_fachada(v) -> str | None:
    """FechaInicioFachada ('aaaa-mm-dd hh:mm:ss', hora de Lima): cuando el técnico inició la instalación."""
    v = _texto(v)
    if v is None:
        return None
    try:
        t = pd.Timestamp(v) if not isinstance(v, str) or re.match(r"^\d{4}-", v.strip()) else _ts(v)
        return t.strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"
    except (ValueError, TypeError):
        return None


def _coord_txt(v) -> str | None:
    """Limpia una latitud/longitud escrita a mano y la devuelve como texto con todos sus decimales:
    ' -12.0860363,' → '-12.0860363' · '-12,0079530' → '-12.0079530' · '-.77.040110' → '-77.040110'."""
    t = _texto(v)
    if t is None:
        return None
    t = str(t).strip()
    if "." not in t and t.count(",") == 1 and re.search(r"\d,\d", t):  # coma decimal
        t = t.replace(",", ".")
    m = re.search(r"-?\s*[\d.]*\d[\d.]*", t)
    if not m:
        return None
    neg = m[0].lstrip().startswith("-")
    num = re.sub(r"[^\d.]", "", m[0]).strip(".")
    if num.count(".") > 1:  # puntos de más: se deja solo el primero
        a, b = num.split(".", 1)
        num = a + "." + b.replace(".", "")
    if not num or not re.fullmatch(r"\d+(\.\d+)?", num):
        return None
    return ("-" if neg else "") + num


def _coord(v):
    t = _coord_txt(v)
    try:
        x = float(t) if t else None
        return x if x is not None and math.isfinite(x) and x != 0 else None
    except ValueError:
        return None


def _coordenadas(lat_v, lon_v) -> tuple[float | None, float | None, str | None]:
    """Latitud y longitud del abonado en el formato pedido por operaciones: '-12.092068781701789,\t-77.01055209096951'
    (todos los decimales, coma y tabulación). Si vienen cruzadas (latitud en la longitud) se ordenan."""
    lt, ln = _coord_txt(lat_v), _coord_txt(lon_v)
    lat, lon = _coord(lt), _coord(ln)
    if lat is None or lon is None:
        return None, None, None
    if abs(lat) > abs(lon):  # en Perú |latitud| < |longitud|: si no, están cruzadas
        lat, lon, lt, ln = lon, lat, ln, lt
    if not (-20 <= lat <= 2 and -83 <= lon <= -66):
        return None, None, None
    return lat, lon, f"{lt},\t{ln}"


OBLIGATORIAS = {"IdOS", "Nro OS", "Tipo Orden", "Estado Os", "F. Recep."}


def leer(ruta: Path, **kw) -> pd.DataFrame:
    """read_excel con el lector rápido (calamine, ~10× más rápido en reportes de 400 mil filas); si no está
    disponible o falla, se usa openpyxl como antes."""
    try:
        return pd.read_excel(ruta, engine="calamine", **kw)
    except Exception as e:  # noqa: BLE001
        if not isinstance(e, (ImportError, ValueError)):
            print(f"Aviso: lector rápido falló ({e}); se usa openpyxl.", flush=True)
        return pd.read_excel(ruta, engine="openpyxl", **kw)


def leer_excel(ruta: Path) -> pd.DataFrame:
    df = leer(ruta, header=FILA_ENCABEZADO, dtype={"Hora": str, "Hora I": str, "Hora F": str, "Hora Mod": str, "fecha_observacion": str, "Observaciones": str})
    faltan = [c for c in [*COLUMNAS, "F. Recep.", "Hora", "F. Atenc.", "Hora I", "Hora F", "Fecha Mod", "Hora Mod", "fecha_observacion", *FECHAS, "Observaciones"] if c not in df.columns]
    criticas = [c for c in faltan if c in OBLIGATORIAS]
    if criticas:
        raise ErrorFormato(f"El reporte ya no trae estas columnas: {criticas}")
    if faltan:  # columnas secundarias: se guardan vacías y se avisa en el registro
        print(f"Aviso: el reporte no trae {faltan}; se guardan vacías.", flush=True)
        for c in faltan:
            df[c] = None
    return df


# Tipo de orden según TIPO_AB.xlsx de operaciones (columna DETALLE): AVERIA, TC, INSTALACION, TRASLADO EXT, …
# Una regla aplica si coinciden el Tipo Orden y, cuando la regla los indica, el MOTIVO y el LUGAR AVERIA.
_REGLAS_DETALLE = json.loads((Path(__file__).with_name("tipo_ab.json")).read_text(encoding="utf-8"))["reglas"]


def detalle_de(tipo, motivo, lugar) -> str | None:
    t, mo, lu = (str(_texto(x) or "").strip().upper() for x in (tipo, motivo, lugar))
    for r in _REGLAS_DETALLE:
        if r["tipo"] == t and (not r["motivo"] or r["motivo"] == mo) and (not r["lugar"] or r["lugar"] == lu):
            return r["detalle"]
    return None


def a_filas(df: pd.DataFrame, sincronizado_en: str, cajas=None) -> list[dict]:
    """cajas: índice de cajas de planta (sync.cajas). Si es None no se tocan las cajas guardadas."""
    filas: list[dict] = []
    for registro in df.to_dict("records"):
        fila = {}
        for origen, destino in COLUMNAS.items():
            v = _texto(registro.get(origen))
            if destino in ENTEROS and v is not None:
                v = int(v)
            elif destino == "fecha_ejecucion" and v is not None:
                v = _ts(v).strftime("%Y-%m-%d")
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            fila[destino] = v
        for destino, (col_f, col_h, requiere_hora) in FECHA_HORA.items():
            if requiere_hora and not _texto(registro.get(col_h)):
                fila[destino] = None
            else:
                fila[destino] = _combinar(registro.get(col_f), registro.get(col_h))
        fila["fecha_observacion"] = _fecha_obs(registro.get("fecha_observacion"))
        for origen, destino in FECHAS.items():
            fila[destino] = _fecha(registro.get(origen))
        fila["hora_recepcion"] = _hora(registro.get("Hora"))
        fila["inicio_fachada"] = _inicio_fachada(registro.get("FechaInicioFachada"))
        fila.update(_agenda(registro.get("Observaciones")))
        # Observaciones de la OS (llamada de ATC), todos los tipos de orden: ficha del AB y 1.ª/2.ª visita o VD; teléfonos ocultos.
        fila["observaciones"] = _sin_tel(str(_texto(registro.get("Observaciones")) or "").strip())[:2000] or None
        # Comentario sin fecha (#dd/mm/aaaa#): HOY / MAÑANA / día de semana se cuentan desde la última modificación de
        # la OS, solo si sigue pendiente (en las ya descargadas esa fecha es posterior al comentario).
        _fm = pd.to_datetime(registro.get("Fecha Mod"), errors="coerce", dayfirst=isinstance(registro.get("Fecha Mod"), str)) \
            if _texto(registro.get("Estado Os")) != "DESCARGADA" else pd.NaT
        fila.update(_obs_visita(registro.get("OBS VISITA TÉCNICO"), _fm.to_pydatetime().replace(hour=0, minute=0, second=0) if pd.notna(_fm) else None))
        # Comentario completo del técnico (todas las anotaciones), solo para las altas; teléfonos ocultos.
        _todo = str(_texto(registro.get("OBS VISITA TÉCNICO")) or "").strip()
        fila["detalle"] = detalle_de(fila.get("tipo_orden"), fila.get("motivo"), fila.get("lugar_averia"))
        fila["obs_visita_todo"] = (_sin_tel(_todo)[:4000] or None) if fila.get("tipo_orden") in ("INSTALACION SERVICIO", "SERVICIO TECNICO") else None
        # Coordenadas del abonado (Latitud MAPS / Longitud MAPS): solo para las altas (INSTALACION SERVICIO),
        # limpias y en el formato "-12.092068781701789,<tab>-77.01055209096951" (todos los decimales).
        lat, lon, txt_coord = _coordenadas(registro.get("Latitud MAPS"), registro.get("Longitud MAPS"))
        es_inst = fila.get("tipo_orden") == "INSTALACION SERVICIO"
        con_coord = es_inst and txt_coord is not None
        fila["coordenadas"] = txt_coord if (es_inst or fila.get("tipo_orden") == "SERVICIO TECNICO") else None
        if cajas is not None:
            fila["cajas_cerca"] = cajas.cerca(lat, lon) if con_coord else None
        fila["sincronizado_en"] = sincronizado_en
        if fila["id_os"] is None:
            continue
        filas.append(fila)
    return filas


_RE_SPEED = re.compile(r"speedtest|ookla|fast\.com|nperf|testdevelocidad", re.I)


def diagnostico_speedtest(df: pd.DataFrame) -> dict:
    """Temporal: qué columnas del Excel traen enlaces de pruebas de velocidad, con unos ejemplos (teléfonos ocultos)."""
    cols = {}
    inst = df[df.get("Tipo Orden", pd.Series(dtype=str)).astype(str).str.upper().eq("INSTALACION SERVICIO")] if "Tipo Orden" in df else df
    for c in df.columns:
        s = inst[c].dropna().astype(str)
        m = s[s.str.contains(_RE_SPEED)]
        if len(m):
            cols[str(c)] = {"filas": int(len(m)), "ejemplos": [_sin_tel(v)[:500] for v in m.head(12).tolist()]}
    return {"columnas_excel": [str(c) for c in df.columns], "instalaciones": int(len(inst)), "con_speedtest": cols}


def procesar(ruta: Path, sincronizado_en: str, cajas=None) -> list[dict]:
    return a_filas(leer_excel(ruta), sincronizado_en, cajas)
