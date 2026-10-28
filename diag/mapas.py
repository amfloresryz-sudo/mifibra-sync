"""Diagnóstico: qué geometrías traen los mapas de planta (solo conteos y nombres de carpetas; sin coordenadas)."""
import collections, os, xml.etree.ElementTree as ET, requests
OUT = []
def print(*a, **k): OUT.append(" ".join(str(x) for x in a))
from sync import cajas
t = cajas._tag
for mid in cajas._mapas():
    r = requests.get(f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1", timeout=90); r.raise_for_status()
    raiz = ET.fromstring(r.content); cnt = collections.Counter(); ej = collections.defaultdict(list); pts = collections.Counter()
    doc = next((e for e in raiz.iter() if t(e) == "Document"), raiz)
    nd = next((c.text.strip() for c in doc if t(c) == "name" and c.text), "?")
    def rec(n, carp):
        for h in n:
            g = t(h)
            if g == "Folder": rec(h, next((c.text.strip() for c in h if t(c) == "name" and c.text), "?"))
            elif g == "Placemark":
                geo = next((t(e) for e in h.iter() if t(e) in ("Point", "LineString", "Polygon", "MultiGeometry")), "?")
                cnt[(carp, geo)] += 1
                co = sum(len((e.text or "").split()) for e in h.iter() if t(e) == "coordinates"); pts[(carp, geo)] += co
                nom = next((c.text.strip() for c in h if t(c) == "name" and c.text), "")
                if geo != "Point" and len(ej[(carp, geo)]) < 4: ej[(carp, geo)].append(nom[:30])
            elif g == "Document": rec(h, carp)
    rec(doc, "(raíz)")
    print(f"=== Mapa «{nd}» ({len(r.content)//1024} KB)")
    for (c, g), v in sorted(cnt.items()): print(f"  {c!r:40} {g:14} {v:6} figuras · {pts[(c, g)]:7} vértices · ej: {ej.get((c, g), [])}")

u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ajustes?on_conflict=clave"; k = os.environ["SUPABASE_SERVICE_KEY"]
requests.post(u, json={"clave": "diag:mapas", "datos": {"t": OUT}}, headers={"apikey": k, "Authorization": f"Bearer {k}", "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=60).raise_for_status()
