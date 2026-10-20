-- Ajustes que operaciones edita desde el portal (por filial): metas, nombres de EECC,
-- gestor de cada EECC y rutas que asisten cada día. Los lee y guarda /api/ajustes.
create table if not exists public.ajustes (
  clave text primary key,            -- 'instalaciones:LIMA'
  datos jsonb not null default '{}'::jsonb,
  actualizado timestamptz not null default now()
);
alter table public.ajustes enable row level security;
revoke all on public.ajustes from anon, authenticated;

-- Coordenadas de las altas (formato "-12.203475, -76.985557") para la ficha del AB.
alter table ordenes_servicio add column if not exists coordenadas text;
-- Agenda (día|franja) que trae el último comentario del técnico, para clasificar agenda / coordinada.
alter table ordenes_servicio add column if not exists visita_agenda text;
notify pgrst, 'reload schema';
