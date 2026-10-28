"""Carga única de las áreas de los planos en el tablero «areas» (lo mismo que hace el robot en cada actualización)."""
import datetime, os, requests
from sync import areas
ar = areas.cargar(datetime.datetime.now(datetime.timezone.utc).isoformat())
if ar:
    u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/tableros?on_conflict=clave"; k = os.environ["SUPABASE_SERVICE_KEY"]
    requests.post(u, json={"clave": "areas", "datos": ar, "actualizado": ar["corte"]}, timeout=120,
                  headers={"apikey": k, "Authorization": f"Bearer {k}", "Prefer": "resolution=merge-duplicates,return=minimal"}).raise_for_status()
