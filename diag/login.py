"""Diagnóstico: UN solo intento de inicio de sesión. Guarda capturas y texto (sin la clave) en ajustes 'diag:login'."""
import base64, json, os, requests
from playwright.sync_api import sync_playwright
from sync.descargar import iniciar_sesion

base = os.environ.get("SCORDSOFT_URL") or "https://mifibra.scordsoft.net"
clave = os.environ.get("SCORDSOFT_CLAVE", "")
info = {"len_clave": len(clave), "espacios_borde": clave != clave.strip(), "len_usuario": len(os.environ.get("SCORDSOFT_USUARIO", "")),
        "totp": bool(os.environ.get("SCORDSOFT_TOTP_SECRETO")), "pasos": []}
caps = {}
with sync_playwright() as p:
    b = p.chromium.launch(); page = b.new_page(viewport={"width": 1300, "height": 900})
    try:
        iniciar_sesion(page, base)
        info["pasos"].append("login terminó sin excepción")
    except Exception as e:
        info["pasos"].append(f"excepción: {type(e).__name__}: {e}"[:500])
    page.wait_for_timeout(4000)
    info["url"] = page.url
    try: info["texto"] = page.inner_text("body")[:2500]
    except Exception as e: info["texto"] = str(e)
    caps["final"] = base64.b64encode(page.screenshot(full_page=True)).decode()
    b.close()
info["cap"] = caps["final"]
u = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ajustes?on_conflict=clave"
k = os.environ["SUPABASE_SERVICE_KEY"]
r = requests.post(u, headers={"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json",
                  "Prefer": "resolution=merge-duplicates"}, data=json.dumps({"clave": "diag:login", "datos": info}))
print(r.status_code, info["pasos"], info["url"], info["len_clave"], info["espacios_borde"])
