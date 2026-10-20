-- Filtro por filial en el portal. Ejecútalo una vez en el SQL Editor de Supabase (se puede repetir).
-- Las vistas de conteo pasan a tener la columna "filial" y los indicadores salen de una función por filial.

drop view if exists public.v_tipo_estado;
create view public.v_tipo_estado as
select filial, tipo_orden, estado_os, count(*)::int as ordenes
from public.v_periodo
group by filial, tipo_orden, estado_os;

drop view if exists public.v_ordenes_dia;
create view public.v_ordenes_dia as
select filial,
       (fecha_registro at time zone 'America/Lima')::date as dia,
       count(*) filter (where tipo_orden = 'INSTALACION SERVICIO')::int as instalaciones,
       count(*) filter (where tipo_orden = 'SERVICIO TECNICO')::int     as servicio_tecnico
from public.v_periodo
group by 1, 2;

drop view if exists public.v_ranking_tecnicos;
create view public.v_ranking_tecnicos as
select filial, tecnico_ejecutor,
       mode() within group (order by contrata) as contrata,
       count(*) filter (where tipo_orden = 'INSTALACION SERVICIO')::int as instalaciones,
       count(*) filter (where tipo_orden = 'SERVICIO TECNICO')::int     as servicio_tecnico,
       count(*) filter (where tipo_orden in ('INSTALACION SERVICIO', 'SERVICIO TECNICO'))::int as total
from public.v_periodo
where estado_os = 'DESCARGADA' and tecnico_ejecutor is not null
group by filial, tecnico_ejecutor;

-- Indicadores de las tarjetas para una filial (misma forma que v_indicadores).
create or replace function public.f_indicadores(p_filial text)
returns setof public.v_indicadores
language sql stable
as $$
with p as (select * from public.v_periodo where filial = p_filial),
     i as (select * from p where tipo_orden = 'INSTALACION SERVICIO'),
     s as (select * from p where tipo_orden = 'SERVICIO TECNICO'),
     o as (select * from p where estado_os = 'OBSERVADA'),
     a as (select * from p where estado_os = 'ANULADA')
select
  (select min(fecha_registro) from p)                                  as periodo_desde,
  (select max(fecha_registro) from p)                                  as periodo_hasta,
  (select count(*) from p)::int                                        as total_ordenes,
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
  (select max(fin) from public.sincronizaciones where ok)                             as actualizado_en
$$;

create index if not exists ordenes_filial_registro_idx on public.ordenes_servicio (filial, fecha_registro);

alter view public.v_tipo_estado set (security_invoker = on);
alter view public.v_ordenes_dia set (security_invoker = on);
alter view public.v_ranking_tecnicos set (security_invoker = on);
revoke all on public.v_tipo_estado, public.v_ordenes_dia, public.v_ranking_tecnicos from anon, authenticated;
revoke execute on function public.f_indicadores(text) from public, anon, authenticated;
grant execute on function public.f_indicadores(text) to service_role;
