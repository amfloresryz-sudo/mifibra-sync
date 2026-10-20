-- Observaciones de la OS (texto de la llamada de ATC, teléfonos ocultos): ficha del AB y 1.ª / 2.ª visita o VD.
alter table ordenes_servicio add column if not exists observaciones text;
