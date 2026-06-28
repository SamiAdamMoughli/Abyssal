-- =============================================================================
-- VesselX — PostgreSQL extension bootstrap
-- Run automatically by the postgis/postgis Docker image via
-- /docker-entrypoint-initdb.d/01_init.sql
-- =============================================================================

-- PostGIS: spatial types, ST_Within, ST_DWithin, ST_MakeEnvelope, etc.
CREATE EXTENSION IF NOT EXISTS postgis;

-- pg_trgm: trigram index for fuzzy vessel-name matching on IUU/sanctions tables.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent: normalises accented characters before trigram comparison.
CREATE EXTENSION IF NOT EXISTS unaccent;
