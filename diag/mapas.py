"""Diagnóstico: áreas del mapa de planos (se guardan en ajustes «diag:areas» para la vista previa)."""
import collections, os, traceback, requests
u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ajustes?on_conflict=clave"; k = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": k, "Authorization": f"Bearer {k}", "Prefer": "resolution=merge-duplicates,return=minimal"}
try:
    from sync import areas
    mid = os.environ["AREAS_MAPAS"]
    r = requests.get(f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1", timeout=90)
    info = {"status": r.status_code, "kb": len(r.content) // 1024, "tipo": r.headers.get("content-type"), "ini": r.content[:200].decode("utf-8", "replace")}
    A = areas._parsear(r.content) if r.ok else []
    cnt = collections.Counter((f["c"], f["t"]) for f in A)
    res = {"info": info, "n": len(A), "por": [[c, t, v] for (c, t), v in cnt.items()], "vert": sum(len(f["p"]) for f in A), "areas": A}
except Exception:
    res = {"error": traceback.format_exc()[-1500:]}
x = requests.post(u, json={"clave": "diag:areas", "datos": res}, headers=H, timeout=120)
if not x.ok:
    requests.post(u, json={"clave": "diag:areas", "datos": {"post": x.status_code, "msg": x.text[:500], "n": res.get("n"), "vert": res.get("vert"), "info": res.get("info")}}, headers=H, timeout=60)
