set -u
S=$(date +%s)
python -u -m sync.main --solo-descargar r/rap.xlsx --solo-mes-actual || exit 1
D=$(date +%s)
python -u -m sync.main --solo-descargar-expres r/exp || exit 1
E=$(date +%s)
python -u -m sync.main --excel r/rap.xlsx --sin-subir 2>&1 | grep -E "órdenes en el reporte|\[tiempo\]" > r/t1.txt
P1=$(date +%s)
python -u -m sync.main --base r/rap.xlsx --parche r/exp/*.xlsx --sin-subir 2>&1 | grep -E "Exprés|órdenes en el reporte|\[tiempo\]" > r/t2.txt
P2=$(date +%s)
python -u -m sync.main --base r/rap.xlsx --parche r/exp/*.xlsx --sin-subir 2>&1 | grep -E "\[tiempo\]" > r/t3.txt
P3=$(date +%s)
python - <<PY
import json, os, requests
L=lambda f: open(f).read()
info={"desc_rapida_s":$D-$S,"desc_expres_s":$E-$D,"proc_normal_s":$P1-$E,"proc_expres_1ra_s":$P2-$P1,"proc_expres_cache_s":$P3-$P2,
 "t1":L("r/t1.txt"),"t2":L("r/t2.txt"),"t3":L("r/t3.txt")}
u=os.environ["SUPABASE_URL"].rstrip("/")+"/rest/v1/ajustes?on_conflict=clave"; k=os.environ["SUPABASE_SERVICE_KEY"]
requests.post(u,headers={"apikey":k,"Authorization":f"Bearer {k}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates"},data=json.dumps({"clave":"diag:expres2","datos":info}))
print(info)
PY
