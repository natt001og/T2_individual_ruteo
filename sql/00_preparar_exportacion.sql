-- Prepara el PGA agregado del enlace troncal sintetico (id_arista=74450).
-- Su PGA fue interpolada desde USGS ShakeMap en el punto medio del enlace.
-- Se toma el valor ya validado en probabilidades_falla_2010 y se conserva
-- como dato de entrada para poder regenerar las probabilidades en una BD limpia.

INSERT INTO pga_enlaces_2010 (
    id_arista, pga_maxima, pga_promedio,
    total_segmentos, segmentos_con_pga, longitud_evaluada_m
)
SELECT
    p.id_arista,
    p.pga_maxima,
    p.pga_promedio,
    GREATEST(COALESCE(p.total_segmentos, 1), 1),
    1,
    ST_Length(a.geom::geography)
FROM probabilidades_falla_2010 p
JOIN red_integrada_aristas_conectada_v1 a ON a.id = p.id_arista
WHERE p.id_arista = 74450
ON CONFLICT (id_arista) DO UPDATE SET
    pga_maxima = EXCLUDED.pga_maxima,
    pga_promedio = EXCLUDED.pga_promedio,
    total_segmentos = EXCLUDED.total_segmentos,
    segmentos_con_pga = EXCLUDED.segmentos_con_pga,
    longitud_evaluada_m = EXCLUDED.longitud_evaluada_m;

-- Debe devolver 74450.
SELECT COUNT(*) AS enlaces_con_pga_agregado
FROM pga_enlaces_2010;

-- Debe devolver el enlace sintetico con PGA aproximada 0.375142 g.
SELECT id_arista, pga_maxima, longitud_evaluada_m
FROM pga_enlaces_2010
WHERE id_arista = 74450;
