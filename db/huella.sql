-- Huella de cada orden: el robot solo reescribe las órdenes que cambiaron (alivia el disco de Supabase).
alter table public.ordenes_servicio add column if not exists huella text;
