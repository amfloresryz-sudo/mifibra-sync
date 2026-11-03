"""Medición: UN inicio de sesión, pide hoy por Ejecución y por Registro (2 días) y mide tiempos. Guarda en ajustes 'diag:expres'."""
import json, os, time, requests
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from sync.descargar import descargar_reportes
from sync.procesar import leer

hoy = datetime.now(timezone(timedelta(hours=-5))).date()
info = {"inicio": datetime.now(timezone.utc).isoformat(), "pruebas": []}
pedidos = [(Path("x/ejec_hoy.xlsx"), hoy, hoy, "TODAS", "ejecucion"),
           (Path("x/reg_2d.xlsx"), hoy - timedelta(days=2), hoy, "TODAS", "registro")]
t0 = time.time()
try:
    rutas = descargar_reportes(pedidos, espera_max_s=600)
    info["total_s"] = round(time.time() - t0)
except Exception as e:
    info["error"] = f"{type(e).__name__}: {e}"[:400]; rutas = []
for r in rutas:
    d = {"archivo": r.name, "kb": r.stat().st_size // 1024}
    try:
        df = leer(r, header=5)
        d["filas"] = len(df); d["cols"] = [str(c) for c in df.columns][:80]
        for c in df.columns:
            if "estado" in str(c).lower() or "tipo" in str(c).lower():
                d.setdefault("vc", {})[str(c)] = {str(k): int(v) for k, v in df[c].astype(str).value_counts().head(15).items()}
    except Exception as e:
        d["err_leer"] = str(e)[:200]
    info["pruebas"].append(d)
u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ajustes?on_conflict=clave"
k = os.environ["SUPABASE_SERVICE_KEY"]
requests.post(u, headers={"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json",
              "Prefer": "resolution=merge-duplicates"}, data=json.dumps({"clave": "diag:expres", "datos": info}, default=str))
print(json.dumps({k: v for k, v in info.items() if k != "pruebas"}))
