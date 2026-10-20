"""Cajas (NAP/FAT/CTO) de planta cercanas a cada alta, a partir de los mapas de Google My Maps.

Los mapas los mantiene operaciones en Google My Maps (los mismos que usa pendientes-op-lima).
El robot los descarga en cada sincronización y, para cada orden de instalación, guarda solo la
lista de cajas a 200 m o menos (nombre, distancia, mapa y carpeta). Las coordenadas del abonado
se usan en memoria para medir la distancia y NO se guardan.
"""
from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET

import requests

# IDs de los mapas de Google My Maps (separados por coma) en la variable CAJAS_MAPAS de GitHub Actions.
# No van en el código porque el repositorio es público y los mapas muestran la ubicación de la planta.
MAPAS = [m.strip() for m in os.environ.get("CAJAS_MAPAS", "").split(",") if m.strip()]
RADIO_M = 200
MAXIMO = 15
_CELDA = 0.002  # grados (~220 m): con la celda vecina basta para un radio de 200 m


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _parsear(texto: bytes, mapa_por_defecto: str) -> list[tuple[str, float, float, str, str]]:
    """Devuelve [(nombre, lat, lon, mapa, carpeta)] de los Placemark con <Point>."""
    raiz = ET.fromstring(texto)
    doc = next((e for e in raiz.iter() if _tag(e) == "Document"), raiz)
    nombre_doc = next((c.text.strip() for c in doc if _tag(c) == "name" and c.text), mapa_por_defecto)
    puntos = []

    def recorrer(nodo, carpeta):
        for hijo in nodo:
            t = _tag(hijo)
            if t == "Folder":
                n = next((c.text.strip() for c in hijo if _tag(c) == "name" and c.text), "(sin carpeta)")
                recorrer(hijo, n)
            elif t == "Placemark":
                nom = next((c.text.strip() for c in hijo if _tag(c) == "name" and c.text), "")
                coord = next((e.text for e in hijo.iter() if _tag(e) == "coordinates" and e.text), None)
                if not coord or not any(_tag(e) == "Point" for e in hijo.iter()):
                    continue
                try:
                    lon, lat = (float(x) for x in coord.strip().split(",")[:2])
                except ValueError:
                    continue
                puntos.append((nom, lat, lon, nombre_doc, carpeta))
            elif t in ("Document",):
                recorrer(hijo, carpeta)

    recorrer(doc, "(sin carpeta)")
    return puntos


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Indice:
    def __init__(self, puntos):
        self.n = len(puntos)
        self.celdas: dict[tuple[int, int], list] = {}
        for p in puntos:
            self.celdas.setdefault((math.floor(p[1] / _CELDA), math.floor(p[2] / _CELDA)), []).append(p)

    def cerca(self, lat: float, lon: float, radio: float = RADIO_M, maximo: int = MAXIMO) -> list[list]:
        ci, cj = math.floor(lat / _CELDA), math.floor(lon / _CELDA)
        hallados = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for nom, la, lo, mapa, carpeta in self.celdas.get((ci + di, cj + dj), ()):
                    d = _dist_m(lat, lon, la, lo)
                    if d <= radio:
                        hallados.append([nom, round(d), mapa, carpeta])
        hallados.sort(key=lambda x: x[1])
        return hallados[:maximo]


def cargar(timeout: int = 60) -> Indice | None:
    """Descarga los mapas. Si alguno no se pudo leer devuelve None y no se tocan las cajas guardadas: con mapas a
    medias cambiarían las cajas de miles de altas y se reescribirían todas sin motivo."""
    if not MAPAS:
        print("Aviso: falta la variable CAJAS_MAPAS; se conservan las cajas ya guardadas.", flush=True)
        return None
    puntos = []
    fallos = 0
    for mid in MAPAS:
        try:
            r = requests.get(f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1", timeout=timeout)
            r.raise_for_status()
            p = _parsear(r.content, mid)
            puntos.extend(p)
            print(f"Mapa de cajas {mid}: {len(p)} cajas.", flush=True)
        except Exception as e:  # un mapa caído no detiene la sincronización
            fallos += 1
            print(f"Aviso: no se pudo leer el mapa de cajas {mid}: {e}", flush=True)
    return Indice(puntos) if puntos and not fallos else None
