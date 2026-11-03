-- Runs once, as superuser, when the Postgres volume is first created.
-- owner:      owns the tables and runs migrations. Subject to FORCE RLS.
-- admin:      BYPASSRLS, used only by seeding and test fixtures. Never by a request path.
-- app:        what the MCP server and API connect as. Non-owner, no BYPASSRLS.
-- checkpoint: LangGraph checkpoints only, in their own schema. No billing access.
CREATE ROLE adjudicate_owner LOGIN PASSWORD 'owner_dev_pw' NOSUPERUSER NOBYPASSRLS CREATEDB;
CREATE ROLE adjudicate_admin LOGIN PASSWORD 'admin_dev_pw' NOSUPERUSER BYPASSRLS;
CREATE ROLE adjudicate_app LOGIN PASSWORD 'app_dev_pw' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE adjudicate_checkpoint LOGIN PASSWORD 'checkpoint_dev_pw' NOSUPERUSER NOBYPASSRLS;

CREATE EXTENSION IF NOT EXISTS vector;
ALTER DATABASE adjudicate OWNER TO adjudicate_owner;
ALTER SCHEMA public OWNER TO adjudicate_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA checkpoints AUTHORIZATION adjudicate_checkpoint;
ALTER ROLE adjudicate_checkpoint SET search_path = checkpoints;
