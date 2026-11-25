-- Build the unified, enriched event table for ONE month.
-- Inputs (views created by transform.py):
--   stg_usgs, stg_emsc  : typed, deduplicated per-source events for the month
--   cities              : GeoNames cities around the region
-- Placeholders {MATCH_SECONDS}, {MATCH_KM}, {HOME_LAT}, {HOME_LON} come from config.py.

WITH pairs AS (
    -- candidate matches: the same physical earthquake reported by both agencies
    SELECT
        e.source_event_id AS emsc_id,
        u.source_event_id AS usgs_id,
        abs(epoch(e.event_time) - epoch(u.event_time))      AS dt_s,
        hav_km(e.lat, e.lon, u.lat, u.lon)                  AS dist_km
    FROM stg_emsc e
    JOIN stg_usgs u
      ON abs(epoch(e.event_time) - epoch(u.event_time)) <= {MATCH_SECONDS}
    WHERE hav_km(e.lat, e.lon, u.lat, u.lon) <= {MATCH_KM}
),
ranked AS (
    -- one-to-one: best partner on both sides (10 km of distance ~ 1 s of time)
    SELECT *,
        row_number() OVER (PARTITION BY emsc_id ORDER BY dt_s + dist_km / 10) AS r_e,
        row_number() OVER (PARTITION BY usgs_id ORDER BY dt_s + dist_km / 10) AS r_u
    FROM pairs
),
matches AS (
    SELECT emsc_id, usgs_id FROM ranked WHERE r_e = 1 AND r_u = 1
),
matched AS (
    -- reviewed USGS solution wins; otherwise EMSC (denser regional network, faster)
    SELECT
        'emsc:' || e.source_event_id                                        AS event_id,
        CASE WHEN u.status = 'reviewed' THEN u.event_time ELSE e.event_time END AS event_time,
        CASE WHEN u.status = 'reviewed' THEN u.lat        ELSE e.lat        END AS lat,
        CASE WHEN u.status = 'reviewed' THEN u.lon        ELSE e.lon        END AS lon,
        CASE WHEN u.status = 'reviewed' THEN u.depth_km   ELSE e.depth_km   END AS depth_km,
        CASE WHEN u.status = 'reviewed' THEN u.mag        ELSE e.mag        END AS mag,
        CASE WHEN u.status = 'reviewed' THEN u.mag_type   ELSE e.mag_type   END AS mag_type,
        coalesce(u.place, e.place)                                           AS place,
        e.event_type                                                         AS event_type,
        CASE WHEN u.status = 'reviewed' THEN 'usgs' ELSE 'emsc' END          AS preferred_source,
        'emsc+usgs'                                                          AS sources,
        e.source_event_id AS emsc_id,
        u.source_event_id AS usgs_id,
        e.mag AS mag_emsc,
        u.mag AS mag_usgs,
        greatest(e.updated_at, u.updated_at)                                 AS updated_at
    FROM matches m
    JOIN stg_emsc e ON e.source_event_id = m.emsc_id
    JOIN stg_usgs u ON u.source_event_id = m.usgs_id
),
emsc_only AS (
    SELECT 'emsc:' || source_event_id, event_time, lat, lon, depth_km, mag, mag_type, place,
           event_type, 'emsc', 'emsc', source_event_id, NULL, mag, NULL, updated_at
    FROM stg_emsc WHERE source_event_id NOT IN (SELECT emsc_id FROM matches)
),
usgs_only AS (
    SELECT 'usgs:' || source_event_id, event_time, lat, lon, depth_km, mag, mag_type, place,
           event_type, 'usgs', 'usgs', NULL, source_event_id, NULL, mag, updated_at
    FROM stg_usgs WHERE source_event_id NOT IN (SELECT usgs_id FROM matches)
),
unified AS (
    SELECT * FROM matched
    UNION ALL SELECT * FROM emsc_only
    UNION ALL SELECT * FROM usgs_only
),
nearest AS (
    -- enrichment from the file source: closest city with population >= 15k
    SELECT
        ev.event_id,
        arg_min(c.name,         hav_km(ev.lat, ev.lon, c.lat, c.lon)) AS nearest_city,
        arg_min(c.country_code, hav_km(ev.lat, ev.lon, c.lat, c.lon)) AS country_code,
        min(hav_km(ev.lat, ev.lon, c.lat, c.lon))                     AS nearest_city_km
    FROM unified ev
    CROSS JOIN cities c
    GROUP BY ev.event_id
)
SELECT
    u.*,
    n.nearest_city,
    n.country_code,
    round(n.nearest_city_km, 1)                                   AS nearest_city_km,
    round(hav_km(u.lat, u.lon, {HOME_LAT}, {HOME_LON}), 1)        AS dist_home_km
FROM unified u
LEFT JOIN nearest n USING (event_id)
ORDER BY event_time
