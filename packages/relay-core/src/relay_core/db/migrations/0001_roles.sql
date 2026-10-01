-- Roles are cluster-wide, so they are created only when missing. They start NOLOGIN: `relay migrate` gives them
-- a password (LOGIN) from the environment, so no secret lives in a migration file (contracts §4.5).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_api') THEN
        CREATE ROLE relay_api NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_worker') THEN
        CREATE ROLE relay_worker NOLOGIN;
    END IF;
END
$$;

REVOKE ALL ON SCHEMA relay FROM PUBLIC;
GRANT USAGE ON SCHEMA relay TO relay_api, relay_worker;
