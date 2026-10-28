-- Comentarios que la EECC deja en «Mis pendientes» (casilla propia o junto a una evidencia).
-- 1) Se ven en la ficha del AB de la web principal (Instalaciones · Pendientes).
-- 2) El robot los escribe en ScordSoft; scord_estado guarda si ya se escribió.
create table if not exists public.eecc_comentarios (
  id             bigserial primary key,
  os             bigint not null,
  ab             bigint,
  filial         text not null,
  eecc           text not null,
  usuario        text not null,
  nombre_usuario text,
  texto          text not null,
  origen         text not null default 'casilla' check (origen in ('casilla', 'evidencia')),
  creado_en      timestamptz not null default now(),
  scord_estado   text not null default 'pendiente' check (scord_estado in ('pendiente', 'escrito', 'error')),
  scord_en       timestamptz,
  scord_error    text,
  scord_intentos integer not null default 0
);
create index if not exists eecc_comentarios_os on public.eecc_comentarios (os);
create index if not exists eecc_comentarios_filial on public.eecc_comentarios (filial, creado_en);
create index if not exists eecc_comentarios_scord on public.eecc_comentarios (creado_en) where scord_estado = 'pendiente';
alter table public.eecc_comentarios enable row level security;   -- solo el servidor (service_role) la lee y escribe
