-- Postgres init script run by Docker on first startup.
-- Runs 001_initial.sql to create schema and indexes.

\i /docker-entrypoint-initdb.d/001_initial.sql
