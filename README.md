# mifibra-sync · Portal de Operaciones Mi Fibra

Robot que descarga el reporte **"Cronograma de Trabajo (Órdenes de Servicio)"** de ScordSoft, lo limpia, calcula los
tableros y guarda en Supabase solo lo que cambió. El portal (Vercel) lee esos tableros ya listos.

```
GitHub Actions (8:00 a 0:00, cada 20 min)
  → robot entra a ScordSoft → 1 Excel (todas las filiales, por Registro, mes anterior + mes en curso)
  → limpieza y tableros en memoria
  → Supabase (solo órdenes nuevas o cambiadas + tableros que cambiaron)
                                   ↓
            Portal Mi Fibra (Vercel) ← /api/instalaciones, /api/averias, /api/indicadores, /api/ordenes
```

Todo funciona en planes gratuitos: GitHub Actions (repositorio público = minutos ilimitados), Supabase Free y Vercel.

## Horario

| Hora de Lima | Qué hace |
|---|---|
| 8:00, 8:20, 8:40 … 23:40 y 0:00 | Descarga el reporte del periodo y actualiza el portal (49 veces al día) |

Tres turnos encadenados de ~5 h 30 min (7:40, 12:55 y 18:25) cubren el día aunque GitHub retrase un cron.

## Qué descarga

Un solo reporte por corrida, igual que a mano: **Filial SELECCIONE** (todas) · **Registro** · del **1 del mes anterior**
al **último día del mes en curso** (p. ej. 01/09/2026 – 31/10/2026). Si ScordSoft devolviera una sola filial, el robot
pide las demás en la misma sesión.

Las órdenes que ya no están en el reporte (por ejemplo, las del mes que sale del periodo al empezar un mes nuevo) se
borran de la base: Supabase siempre contiene solo el periodo.

## Por qué así (plan gratuito de Supabase)

El plan gratuito tiene muy poco disco por segundo. Si cada corrida reescribe o relee las ~400 mil órdenes del periodo,
se agota el presupuesto de E/S y el portal deja de responder ("Supabase 504 / statement timeout"). Por eso:

- El robot guarda la **huella** de cada orden en la caché de GitHub Actions (`.estado/`) y solo escribe lo que cambió.
- Los tableros de Instalaciones, Averías e indicadores de la portada se calculan **en memoria** con el Excel recién
  descargado (no se relee la base) y se guardan solo si cambiaron.
- El portal lee filas ya calculadas de la tabla `tableros`; no hay cálculos pesados en cada visita.

## Carpetas

| Archivo | Qué hace |
|---|---|
| `sync/descargar.py` | Robot (Playwright): login, filtros del reporte, Procesar y descarga del Excel. |
| `sync/procesar.py` | Lee el Excel y conserva solo columnas operativas (sin nombres, documentos, direcciones ni teléfonos). |
| `sync/main.py` | Orquesta una corrida y calcula todos los tableros. |
| `sync/resumen.py` | Indicadores de la portada y del Dashboard. |
| `sync/averias.py`, `sync/instalaciones.py` | Tableros de Averías y TC, e Instalaciones. |
| `sync/cajas.py` | Cajas de planta cercanas a cada alta (mapas de Google My Maps). |
| `sync/subir.py` | Escribe en Supabase solo lo que cambió y borra lo que salió del periodo. |
| `api/` | Funciones de Vercel que leen Supabase. |
| `public/` | Páginas del portal. |
| `db/` | SQL de la base. |
| `.github/workflows/sincronizar.yml` | Horario del robot. |

## Configuración

**GitHub → Settings → Secrets and variables → Actions**

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `SCORDSOFT_USUARIO`, `SCORDSOFT_CLAVE` | usuario de servicio de ScordSoft |
| Secret | `SCORDSOFT_TOTP_SECRETO` | solo si usa app autenticadora |
| Secret | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Project URL y service_role key de Supabase |
| Variable | `CAJAS_MAPAS` | IDs de los mapas de cajas, separados por coma (no van en el código: el repo es público) |

**Vercel → Settings → Environment Variables:** `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `PORTAL_TOKEN` (clave del portal)
y `MASTER_TOKEN` (clave master para editar agendas).

## Ejecutar a mano

**Actions → Sincronizar ScordSoft → Run workflow**:
- `una`: una actualización ahora.
- `turno`: actualiza cada 20 min durante ~5 h 30 min.
- `reescribir`: reescribe todas las órdenes (solo si se cambió la limpieza en `procesar.py`; mejor de noche).

## Probar sin robot

```bash
pip install -r requirements.txt
python -m sync.main --excel reporte.xlsx --sin-subir   # solo procesa y calcula
python -m sync.main --excel reporte.xlsx               # procesa y guarda en Supabase
```

## Si ScordSoft cambia

- **Cambia la pantalla del reporte** → ajusta los selectores al inicio de `sync/descargar.py`.
- **Cambian las columnas del Excel** → el robot falla con "El reporte ya no trae estas columnas"; ajusta `COLUMNAS` en `sync/procesar.py`.
