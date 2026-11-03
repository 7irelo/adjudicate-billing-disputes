-- Unset tenant => NULL => no rows. Every policy fails closed.
CREATE FUNCTION current_tenant() RETURNS text
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '') $$;

DO $$
DECLARE t text;
BEGIN
  -- Strictly tenant-owned tables.
  FOREACH t IN ARRAY ARRAY[
    'accounts','tariff_plans','tariff_rates','bundles','subscriptions','subscription_events',
    'invoices','invoice_lines','cdrs','notifications','consents','payments','credit_rules',
    'disputes','review_items','credit_note_lines'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant())
       WITH CHECK (tenant_id = current_tenant())', t);
  END LOOP;

  -- Credit notes: the same isolation, and the app can only ever write a draft.
  ALTER TABLE credit_notes ENABLE ROW LEVEL SECURITY;
  ALTER TABLE credit_notes FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON credit_notes USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant() AND status = 'draft');

  -- Corpus tables: tenant rows plus global rows (NULL tenant) are readable.
  -- Global rows can't be written through this policy.
  FOREACH t IN ARRAY ARRAY['documents','clauses','chunks'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_or_global ON %I
       USING (tenant_id = current_tenant() OR (tenant_id IS NULL AND current_tenant() IS NOT NULL))
       WITH CHECK (tenant_id = current_tenant())', t);
  END LOOP;

  ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_self ON tenants USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());
END $$;

-- Least privilege: no DELETE anywhere, and writes only on workflow tables.
GRANT USAGE ON SCHEMA public TO adjudicate_app, adjudicate_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO adjudicate_app;
REVOKE SELECT ON schema_migrations FROM adjudicate_app;
GRANT INSERT, UPDATE ON disputes, review_items TO adjudicate_app;
GRANT INSERT ON credit_notes, credit_note_lines TO adjudicate_app;
GRANT EXECUTE ON FUNCTION current_tenant() TO adjudicate_app, adjudicate_admin;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO adjudicate_admin;
