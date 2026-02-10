-- Create a user with read/write access to the digitallibrary schema
-- and read-only access to the systemchart schema
-- Run: psql -h HOST -U admin -d postgres -f sql/create_digitallibrary_user.sql

-- Create the role (login-enabled user)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'digitallibrary_rw') THEN
    CREATE ROLE digitallibrary_rw WITH LOGIN PASSWORD 'mypassword';
  ELSE
    ALTER ROLE digitallibrary_rw WITH PASSWORD 'mypassword';
  END IF;
END
$$;

-- Revoke all default access (no access to public schema or other schemas)
REVOKE ALL ON SCHEMA public FROM digitallibrary_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM digitallibrary_rw;

-- Grant access to the digitallibrary schema only
GRANT USAGE ON SCHEMA digitallibrary TO digitallibrary_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA digitallibrary TO digitallibrary_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA digitallibrary TO digitallibrary_rw;

-- Ensure future tables/sequences in digitallibrary are also accessible
ALTER DEFAULT PRIVILEGES IN SCHEMA digitallibrary
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO digitallibrary_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA digitallibrary
  GRANT USAGE, SELECT ON SEQUENCES TO digitallibrary_rw;

-- Grant read-only access to the systemchart schema
GRANT USAGE ON SCHEMA systemchart TO digitallibrary_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA systemchart TO digitallibrary_rw;

-- Ensure future tables in systemchart are also readable
ALTER DEFAULT PRIVILEGES IN SCHEMA systemchart
  GRANT SELECT ON TABLES TO digitallibrary_rw;
