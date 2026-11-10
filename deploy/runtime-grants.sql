-- Run manually as the schema owner/admin after Alembic, in the application DB.
-- No DDL, schema ownership, migration table access, or future blanket grants.
GRANT CONNECT ON DATABASE llama TO llama_runtime;
GRANT USAGE ON SCHEMA public TO llama_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.sessions,
    public.conversations,
    public.messages,
    public.inference_requests,
    public.daily_quotas
TO llama_runtime;
