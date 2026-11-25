-- Modelo A: fragilidad lognormal simplificada.
-- PGA de entrada: pga_enlaces_2010, agregada desde USGS ShakeMap.
-- Parametros adoptados para el ejercicio: theta=0.30 g; beta=0.60.
-- P(falla | PGA) = Phi( ln(PGA/theta) / beta ).
-- Es una adaptacion simplificada; no es una curva calibrada por tipo de cable.

CREATE OR REPLACE FUNCTION public.normal_cdf_2010(x DOUBLE PRECISION)
RETURNS DOUBLE PRECISION
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    t DOUBLE PRECISION;
    z DOUBLE PRECISION;
BEGIN
    IF x < 0 THEN
        RETURN 1.0 - public.normal_cdf_2010(-x);
    END IF;
    t := 1.0 / (1.0 + 0.2316419 * x);
    z := 0.3989422804014327 * EXP(-0.5 * x * x);
    RETURN 1.0 - z * (
        0.319381530 * t
        - 0.356563782 * POWER(t, 2)
        + 1.781477937 * POWER(t, 3)
        - 1.821255978 * POWER(t, 4)
        + 1.330274429 * POWER(t, 5)
    );
END;
$$;

DO $$
DECLARE
    n_red BIGINT;
    n_pga BIGINT;
BEGIN
    SELECT COUNT(*) INTO n_red FROM public.red_integrada_aristas_conectada_v1;
    SELECT COUNT(*) INTO n_pga FROM public.pga_enlaces_2010;
    IF n_red <> 74450 THEN
        RAISE EXCEPTION 'Se esperaban 74450 aristas conectadas; se encontraron %', n_red;
    END IF;
    IF n_pga <> 74450 THEN
        RAISE EXCEPTION 'Se esperaban 74450 registros PGA; se encontraron %', n_pga;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.pga_enlaces_2010
        WHERE id_arista = 74450 AND pga_maxima IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Falta el PGA del enlace sintetico 74450.';
    END IF;
END $$;

DROP TABLE IF EXISTS public.probabilidades_falla_2010;
CREATE TABLE public.probabilidades_falla_2010 AS
WITH calculo AS (
    SELECT
        a.id AS id_arista,
        a.origen,
        a.tipo_enlace,
        a.es_sintetico,
        p.pga_maxima,
        p.pga_promedio,
        p.total_segmentos,
        CASE
            WHEN p.pga_maxima IS NULL OR p.pga_maxima <= 0 THEN NULL
            ELSE public.normal_cdf_2010(LN(p.pga_maxima / 0.30) / 0.60)
        END AS prob_falla,
        CASE WHEN p.pga_maxima IS NULL THEN 'SIN_ESTIMACION'
             ELSE 'USGS_SHAKEMAP' END AS fuente
    FROM public.red_integrada_aristas_conectada_v1 a
    LEFT JOIN public.pga_enlaces_2010 p ON p.id_arista = a.id
)
SELECT *,
    CASE
        WHEN prob_falla IS NULL THEN 'SIN_ESTIMACION'
        WHEN prob_falla >= 0.50 THEN 'ALTO'
        WHEN prob_falla >= 0.20 THEN 'MEDIO'
        ELSE 'BAJO'
    END AS nivel_riesgo
FROM calculo;

ALTER TABLE public.probabilidades_falla_2010
    ADD PRIMARY KEY (id_arista);
CREATE INDEX idx_prob_falla_2010_riesgo
    ON public.probabilidades_falla_2010 (nivel_riesgo);

UPDATE public.red_integrada_aristas_conectada_v1 a
SET prob_falla = p.prob_falla
FROM public.probabilidades_falla_2010 p
WHERE a.id = p.id_arista;

-- La tabla se reconstruye en cada ejecución para que el script sea repetible.
DROP TABLE IF EXISTS public.limitaciones_modelo_sismico;
CREATE TABLE public.limitaciones_modelo_sismico AS
SELECT
    a.id,
    a.origen,
    a.tipo_enlace,
    a.es_sintetico,
    p.prob_falla,
    CASE WHEN p.prob_falla IS NULL THEN 'SIN_ESTIMACION' ELSE 'SHAKEMAP' END AS estado_estimacion,
    ST_Distance(
        ST_PointOnSurface(a.geom)::geography,
        ST_Transform(ST_Force2D(t.geom), 4326)::geography
    ) / 1000.0 AS distancia_km,
    CASE WHEN p.prob_falla IS NULL
         THEN 'Fuera de cobertura util del ShakeMap; no se asigna probabilidad inferida.'
         ELSE 'PGA agregada desde USGS ShakeMap y fragilidad lognormal simplificada.'
    END AS limitacion
FROM public.red_integrada_aristas_conectada_v1 a
LEFT JOIN public.probabilidades_falla_2010 p ON p.id_arista = a.id
CROSS JOIN public.terremoto_2010 t;

ALTER TABLE public.limitaciones_modelo_sismico ADD PRIMARY KEY (id);
CREATE INDEX idx_limitaciones_estado
    ON public.limitaciones_modelo_sismico (estado_estimacion);
ANALYZE public.red_integrada_aristas_conectada_v1;
ANALYZE public.probabilidades_falla_2010;

SELECT
    COUNT(*) AS total_aristas,
    COUNT(prob_falla) AS con_probabilidad,
    COUNT(*) FILTER (WHERE prob_falla IS NULL) AS sin_estimacion,
    ROUND(MIN(prob_falla)::numeric, 5) AS prob_minima,
    ROUND(MAX(prob_falla)::numeric, 5) AS prob_maxima,
    COUNT(*) FILTER (WHERE prob_falla < 0 OR prob_falla > 1) AS invalidas
FROM public.probabilidades_falla_2010;
