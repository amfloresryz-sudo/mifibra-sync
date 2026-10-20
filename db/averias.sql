-- Módulo Averías y TC. Ejecútalo una vez en el SQL Editor de Supabase (se puede repetir sin problema).
-- Agrega a ordenes_servicio los campos que usa el tablero y crea la tabla donde el robot deja
-- el tablero ya calculado. Las observaciones (texto libre) NO se guardan: solo la agenda extraída.

alter table public.ordenes_servicio
  add column if not exists segmento                  text,     -- B2C / B2B
  add column if not exists tecnico_responsable       text,     -- técnico asignado (pendientes)
  add column if not exists fecha_atencion            date,     -- F. Atenc.
  add column if not exists fecha_instalacion_abonado date,     -- para "infancia"
  add column if not exists hora_recepcion            text,     -- Hora de F. Recep. (HH:MM); vacía = sin hora
  add column if not exists obs_agenda                boolean,  -- observaciones mencionan agenda / primera / segunda visita
  add column if not exists agenda_estado             text,     -- ok / sinformato / vacio
  add column if not exists agenda_texto              text;     -- p. ej. "15-oct. 8 - 10"

create index if not exists ordenes_tipo_registro_idx on public.ordenes_servicio (tipo_orden, fecha_registro);

-- Contrata habitual de cada técnico (la más frecuente en sus órdenes ejecutadas por una EECC).
create or replace view public.v_tecnico_contrata as
select tecnico_ejecutor as tecnico, mode() within group (order by contrata) as contrata
from public.ordenes_servicio
where contrata like 'EECC%' and tecnico_ejecutor is not null
group by tecnico_ejecutor;
alter view public.v_tecnico_contrata set (security_invoker = on);

-- Tableros ya calculados por el robot (uno por módulo).
create table if not exists public.tableros (
  clave        text primary key,                 -- 'averias'
  datos        jsonb not null,
  actualizado  timestamptz not null default now()
);
alter table public.tableros enable row level security;

revoke all on public.v_tecnico_contrata, public.tableros from anon, authenticated;
