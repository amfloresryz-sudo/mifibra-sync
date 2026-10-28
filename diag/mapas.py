"""Diagnóstico: áreas del mapa de planos (se guardan en ajustes «diag:areas» para la vista previa)."""
import collections, os, requests
from sync import areas
ar = areas.cargar("diag") or {}
A = ar.get("areas", [])
cnt = collections.Counter((f["c"], f["t"]) for f in A)
res = {"n": len(A), "por": [[c, t, v] for (c, t), v in cnt.items()], "vert": sum(len(f["p"]) for f in A), "areas": A}
u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ajustes?on_conflict=clave"; k = os.environ["SUPABASE_SERVICE_KEY"]
requests.post(u, json={"clave": "diag:areas", "datos": res}, headers={"apikey": k, "Authorization": f"Bearer {k}", "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=60).raise_for_status()
