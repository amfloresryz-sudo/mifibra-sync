"""Prueba: ¿se puede leer la velocidad de un resultado de speedtest.net desde el robot?"""
import json, re, urllib.request
URLS = [
    "https://www.speedtest.net/my-result/d/7f1668de-8937-49b8-9e9d-3daffdd5bec3",
    "https://www.speedtest.net/my-result/d/00285e54-daac-4837-b444-856e7c16c314",
    "https://www.speedtest.net/result/i/7281239966",
    "https://www.speedtest.net/es/result/d/f49c3cc2-6236-45d7-81e0-e21dbfd6b0d7",
    "https://www.speedtest.net/result/d/059071aa-b211-46d4-b518-b0cab46de61b",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
out = []
for u in URLS:
    r = {"url": u}
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "es-PE,es;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            h = resp.read().decode("utf-8", "replace"); r["status"] = resp.status; r["final"] = resp.geturl()
        r["len"] = len(h)
        for k in ["download", "upload", "ping", "og:description", "og:title", "INIT_DATA", "Mbps"]:
            i = h.find(k); r[k] = h[max(0, i - 150): i + 350] if i >= 0 else None
        r["nums"] = re.findall(r'"(download|upload|ping|latency)"\s*:\s*"?([\d.]+)', h)[:12]
    except Exception as e:
        r["error"] = repr(e)
    out.append(r)
json.dump(out, open("diag/resultado.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps([{k: v for k, v in x.items() if k in ("url", "status", "len", "nums", "error")} for x in out], indent=1))
