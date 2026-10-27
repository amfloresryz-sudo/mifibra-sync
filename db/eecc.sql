-- Portal de contratistas (EECC): usuario ligado a una EECC y evidencias que adjuntan a sus pendientes.

-- 1) Usuario de EECC: con este campo el usuario solo ve «Mis pendientes» de esa EECC.
alter table public.usuarios add column if not exists eecc text;

-- 2) Evidencias (fotos, PDF o video corto) que la EECC adjunta a una OS. El archivo vive en el almacenamiento
--    (bucket «evidencias», privado) y aquí queda el registro. Se borran solos a los 30 días.
create table if not exists public.evidencias (
  id             bigserial primary key,
  os             bigint not null,
  ab             bigint,
  filial         text not null,
  eecc           text not null,
  usuario        text not null,
  nombre_usuario text,
  nombre         text not null,        -- nombre original del archivo
  tipo           text not null,        -- image/jpeg, application/pdf, video/mp4…
  bytes          integer not null,
  comentario     text,
  ruta           text not null unique, -- ruta dentro del bucket
  creado_en      timestamptz not null default now()
);
create index if not exists evidencias_os on public.evidencias (os);
create index if not exists evidencias_creado on public.evidencias (creado_en);
alter table public.evidencias enable row level security;   -- solo el servidor (service_role) la lee y escribe

-- 3) Bucket privado para los archivos (máx. 50 MB por archivo, solo fotos, PDF y video).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('evidencias', 'evidencias', false, 52428800,
        array['image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'video/mp4', 'video/quicktime', 'video/webm'])
on conflict (id) do nothing;

-- 4) Vivienda que la EECC marca a mano en «Mis pendientes»: H (horizontal) o V (vertical), una por OS.
create table if not exists public.eecc_vivienda (
  os          bigint primary key,
  valor       text not null check (valor in ('H', 'V')),
  eecc        text not null,
  usuario     text not null,
  actualizado timestamptz not null default now()
);
alter table public.eecc_vivienda enable row level security;   -- solo el servidor (service_role)
