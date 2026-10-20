-- Esquema para Supabase (Postgres). Ejecútalo una vez en el SQL Editor de Supabase.
-- Guarda solo datos operativos de las órdenes de servicio; sin datos personales del abonado.

create table if not exists public.ordenes_servicio (
  id_os                 bigint primary key,      -- IdOS de ScordSoft (único por orden)
  nro_os                bigint,
  filial                text,
  tipo_orden            text,                    -- INSTALACION SERVICIO, SERVICIO TECNICO, ...
  estado_os             text,                    -- REGISTRADA, DESCARGADA, OBSERVADA, ANULADA
  sub_estado            text,
  abonado_codigo        bigint,                  -- solo el código, no el nombre
  categoria             text,
  tarifa_servicio       text,
  distrito              text,
  fecha_registro        timestamptz,             -- F. Recep. + Hora
  atencion_inicio       timestamptz,             -- F. Atenc. + Hora I
  atencion_fin          timestamptz,             -- F. Atenc. + Hora F
  fecha_ejecucion       date,
  horario_atencion      text,
  tecnico_ejecutor      text,
  contrata              text,                    -- AREA TECNICO EJECUTOR
  motivo                text,
  lugar_averia          text,
  causa                 text,
  sub_causa             text,
  solucion              text,
  motivo_observacion    text,                    -- COMERCIAL / TÉCNICO
  submotivo_observacion text,
  fecha_observacion     timestamptz,
  usuario_observacion   text,
  motivo_anulacion      text,
  usuario_mod           text,
  fecha_mod             timestamptz,
  canal_atencion        text,
  sincronizado_en       timestamptz not null default now()
);

create index if not exists ordenes_fecha_registro_idx on public.ordenes_servicio (fecha_registro);
create index if not exists ordenes_tipo_estado_idx on public.ordenes_servicio (tipo_orden, estado_os);

-- Registro de cada corrida del robot (para saber cuándo se actualizó y si falló).
create table if not exists public.sincronizaciones (
  id           bigserial primary key,
  inicio       timestamptz not null default now(),
  fin          timestamptz,
  modo         text not null,                    -- incremental / completo
  filas        integer,
  ok           boolean,
  mensaje      text
);

-- Nadie lee las tablas directamente desde el navegador: solo el servidor (Vercel y el robot)
-- con la service_role key. Con RLS activo y sin políticas, la clave pública (anon) no ve nada.
alter table public.ordenes_servicio enable row level security;
alter table public.sincronizaciones enable row level security;

-- ---------------------------------------------------------------------------
-- Periodo del portal: mes anterior + mes actual, por fecha de registro (hora de Lima).
-- ---------------------------------------------------------------------------
create or replace view public.v_periodo as
select o.*
from public.ordenes_servicio o
where o.fecha_registro >= (date_trunc('month', (now() at time zone 'America/Lima')) - interval '1 month') at time zone 'America/Lima';

-- Conteos por tipo de orden y estado.
create or replace view public.v_tipo_estado as
select tipo_orden, estado_os, count(*)::int as ordenes
from public.v_periodo
group by tipo_orden, estado_os;

-- Indicadores de las tarjetas del portal (una sola fila).
create or replace view public.v_indicadores as
with i as (select * from public.v_periodo where tipo_orden = 'INSTALACION SERVICIO'),
     s as (select * from public.v_periodo where tipo_orden = 'SERVICIO TECNICO'),
     o as (select * from public.v_periodo where estado_os = 'OBSERVADA'),
     a as (select * from public.v_periodo where estado_os = 'ANULADA')
select
  (select min(fecha_registro) from public.v_periodo)                                  as periodo_desde,
  (select max(fecha_registro) from public.v_periodo)                                  as periodo_hasta,
  (select count(*) from public.v_periodo)::int                                        as total_ordenes,
  -- Instalaciones
  (select count(*) from i)::int                                                       as inst_total,
  (select count(*) from i where estado_os = 'REGISTRADA')::int                        as inst_registradas,
  (select count(*) from i where estado_os = 'DESCARGADA')::int                        as inst_descargadas,
  (select count(*) from i where estado_os = 'OBSERVADA')::int                         as inst_observadas,
  (select count(*) from i where estado_os = 'ANULADA')::int                           as inst_anuladas,
  round(100.0 * (select count(*) from i where estado_os = 'DESCARGADA') / nullif((select count(*) from i), 0), 1) as inst_efectividad_pct,
  -- Servicio técnico (averías)
  (select count(*) from s)::int                                                       as st_total,
  (select count(*) from s where estado_os = 'REGISTRADA')::int                        as st_registradas,
  (select count(*) from s where estado_os = 'DESCARGADA')::int                        as st_descargadas,
  (select count(*) from s where estado_os = 'ANULADA')::int                           as st_anuladas,
  (select round((percentile_cont(0.5) within group (order by extract(epoch from atencion_fin - fecha_registro) / 3600))::numeric, 1)
     from s where estado_os = 'DESCARGADA' and atencion_fin >= fecha_registro)       as st_horas_mediana,
  (select round((avg(extract(epoch from atencion_fin - fecha_registro) / 3600))::numeric, 1)
     from s where estado_os = 'DESCARGADA' and atencion_fin >= fecha_registro)       as st_horas_promedio,
  -- Observadas
  (select count(*) from o)::int                                                       as obs_total,
  (select count(*) from o where motivo_observacion = 'COMERCIAL')::int                as obs_comercial,
  (select count(*) from o where motivo_observacion = 'TÉCNICO')::int                  as obs_tecnico,
  (select submotivo_observacion from o where submotivo_observacion is not null
     group by 1 order by count(*) desc, 1 limit 1)                                    as obs_submotivo_top,
  (select count(*) from o where submotivo_observacion = (
     select submotivo_observacion from o where submotivo_observacion is not null
     group by 1 order by count(*) desc, 1 limit 1))::int                              as obs_submotivo_top_n,
  -- Anuladas
  (select count(*) from a)::int                                                       as anul_total,
  (select motivo_anulacion from a where motivo_anulacion is not null
     group by 1 order by count(*) desc, 1 limit 1)                                    as anul_motivo_top,
  (select count(*) from a where motivo_anulacion = (
     select motivo_anulacion from a where motivo_anulacion is not null
     group by 1 order by count(*) desc, 1 limit 1))::int                              as anul_motivo_top_n,
  (select max(fin) from public.sincronizaciones where ok)                             as actualizado_en;

-- Instalaciones y servicio técnico por día de registro (gráfico de barras).
create or replace view public.v_ordenes_dia as
select (fecha_registro at time zone 'America/Lima')::date as dia,
       count(*) filter (where tipo_orden = 'INSTALACION SERVICIO')::int as instalaciones,
       count(*) filter (where tipo_orden = 'SERVICIO TECNICO')::int     as servicio_tecnico
from public.v_periodo
group by 1
order by 1;

-- Ranking de técnicos por instalaciones descargadas.
create or replace view public.v_ranking_tecnicos as
select tecnico_ejecutor,
       mode() within group (order by contrata) as contrata,
       count(*) filter (where tipo_orden = 'INSTALACION SERVICIO')::int as instalaciones,
       count(*) filter (where tipo_orden = 'SERVICIO TECNICO')::int     as servicio_tecnico,
       count(*) filter (where tipo_orden in ('INSTALACION SERVICIO', 'SERVICIO TECNICO'))::int as total
from public.v_periodo
where estado_os = 'DESCARGADA' and tecnico_ejecutor is not null
group by tecnico_ejecutor
order by instalaciones desc, total desc;

-- Seguridad: las vistas respetan RLS y no son legibles con la clave pública (anon).
alter view public.v_periodo set (security_invoker = on);
alter view public.v_tipo_estado set (security_invoker = on);
alter view public.v_indicadores set (security_invoker = on);
alter view public.v_ordenes_dia set (security_invoker = on);
alter view public.v_ranking_tecnicos set (security_invoker = on);
revoke all on public.ordenes_servicio, public.sincronizaciones, public.v_periodo, public.v_tipo_estado,
  public.v_indicadores, public.v_ordenes_dia, public.v_ranking_tecnicos from anon, authenticated;
