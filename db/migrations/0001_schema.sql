-- Money is integer cents (ZAR). Timestamps are UTC; cut-overs use Africa/Johannesburg in code.
-- Composite foreign keys include tenant_id so a row can't reference another tenant's row.

CREATE TABLE tenants (
  tenant_id text PRIMARY KEY,
  name text NOT NULL,
  customer_type text NOT NULL CHECK (customer_type IN ('consumer', 'business'))
);

CREATE TABLE accounts (
  tenant_id text NOT NULL REFERENCES tenants,
  account_ref text NOT NULL,
  holder_name text NOT NULL,
  customer_type text NOT NULL CHECK (customer_type IN ('consumer', 'business')),
  msisdn text NOT NULL,
  opened_on date NOT NULL,
  scenario_id text,
  PRIMARY KEY (tenant_id, account_ref)
);

CREATE TABLE tariff_plans (
  tenant_id text NOT NULL REFERENCES tenants,
  plan_id text NOT NULL,
  name text NOT NULL,
  monthly_fee_cents bigint NOT NULL CHECK (monthly_fee_cents >= 0),
  PRIMARY KEY (tenant_id, plan_id)
);

CREATE TABLE tariff_rates (
  tenant_id text NOT NULL,
  rate_id text NOT NULL,
  plan_id text NOT NULL,
  destination_class text NOT NULL,
  cents_per_minute bigint NOT NULL CHECK (cents_per_minute >= 0),
  effective_from date NOT NULL,
  effective_to date,
  clause_id text,
  PRIMARY KEY (tenant_id, rate_id),
  FOREIGN KEY (tenant_id, plan_id) REFERENCES tariff_plans
);

CREATE TABLE bundles (
  tenant_id text NOT NULL,
  bundle_id text NOT NULL,
  plan_id text NOT NULL,
  included_minutes integer NOT NULL CHECK (included_minutes >= 0),
  PRIMARY KEY (tenant_id, bundle_id),
  FOREIGN KEY (tenant_id, plan_id) REFERENCES tariff_plans
);

CREATE TABLE subscriptions (
  tenant_id text NOT NULL,
  subscription_id text NOT NULL,
  account_ref text NOT NULL,
  plan_id text NOT NULL,
  started_on date NOT NULL,
  fixed_term_months integer,
  PRIMARY KEY (tenant_id, subscription_id),
  FOREIGN KEY (tenant_id, account_ref) REFERENCES accounts,
  FOREIGN KEY (tenant_id, plan_id) REFERENCES tariff_plans
);

CREATE TABLE subscription_events (
  tenant_id text NOT NULL,
  event_id text NOT NULL,
  subscription_id text NOT NULL,
  event_type text NOT NULL CHECK (event_type IN
    ('activation', 'plan_change', 'cancellation_request', 'cancellation_effective', 'suspension')),
  occurred_on date NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions
);

CREATE TABLE invoices (
  tenant_id text NOT NULL,
  invoice_id text NOT NULL,
  account_ref text NOT NULL,
  period_start date NOT NULL,
  period_end date NOT NULL,
  issued_on date NOT NULL,
  total_cents bigint NOT NULL,
  PRIMARY KEY (tenant_id, invoice_id),
  FOREIGN KEY (tenant_id, account_ref) REFERENCES accounts,
  CHECK (period_end >= period_start)
);

CREATE TABLE invoice_lines (
  tenant_id text NOT NULL,
  line_id text NOT NULL,
  invoice_id text NOT NULL,
  subscription_id text NOT NULL,
  line_type text NOT NULL CHECK (line_type IN
    ('recurring', 'usage_local', 'usage_international', 'usage_roaming', 'usage_oob',
     'once_off', 'cancellation_penalty')),
  description text NOT NULL,
  service_start date NOT NULL,
  service_end date NOT NULL,
  quantity integer NOT NULL DEFAULT 1,
  amount_cents bigint NOT NULL,   -- VAT inclusive
  PRIMARY KEY (tenant_id, line_id),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices,
  FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions
);

CREATE TABLE cdrs (
  tenant_id text NOT NULL,
  cdr_id text NOT NULL,
  subscription_id text NOT NULL,
  started_at timestamptz NOT NULL,
  duration_seconds integer NOT NULL CHECK (duration_seconds >= 0),
  destination text NOT NULL,
  destination_class text NOT NULL,
  direction text NOT NULL DEFAULT 'outbound' CHECK (direction IN ('outbound', 'inbound_missed')),
  billed_cents bigint NOT NULL DEFAULT 0,
  invoice_line_id text,
  PRIMARY KEY (tenant_id, cdr_id),
  FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions,
  FOREIGN KEY (tenant_id, invoice_line_id) REFERENCES invoice_lines (tenant_id, line_id)
);
CREATE INDEX cdrs_sub_time ON cdrs (tenant_id, subscription_id, started_at);

CREATE TABLE notifications (
  tenant_id text NOT NULL,
  notification_id text NOT NULL,
  subscription_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('bundle_depletion', 'bill_shock', 'roaming_welcome')),
  sent_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, notification_id),
  FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions
);

CREATE TABLE consents (
  tenant_id text NOT NULL,
  consent_id text NOT NULL,
  subscription_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('oob_opt_in', 'roaming_activation', 'international_enabled')),
  granted_on date NOT NULL,
  revoked_on date,
  PRIMARY KEY (tenant_id, consent_id),
  FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions
);

CREATE TABLE payments (
  tenant_id text NOT NULL,
  payment_id text NOT NULL,
  invoice_id text NOT NULL,
  paid_on date NOT NULL,
  amount_cents bigint NOT NULL,
  PRIMARY KEY (tenant_id, payment_id),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices
);

CREATE TABLE credit_rules (
  tenant_id text NOT NULL REFERENCES tenants,
  rule_id text NOT NULL,
  version integer NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  definition jsonb NOT NULL,
  PRIMARY KEY (tenant_id, rule_id, version)
);

-- tenant_id NULL means a global regulatory document, readable by every tenant.
CREATE TABLE documents (
  document_id text PRIMARY KEY,
  tenant_id text REFERENCES tenants,
  title text NOT NULL,
  kind text NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  source_sha256 text NOT NULL,
  full_text text NOT NULL
);

CREATE TABLE clauses (
  clause_id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES documents,
  tenant_id text REFERENCES tenants,
  kind text NOT NULL,
  path text[] NOT NULL,
  text text NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  char_start integer NOT NULL,
  char_end integer NOT NULL
);

CREATE TABLE chunks (
  chunk_id text PRIMARY KEY,
  strategy text NOT NULL,
  document_id text NOT NULL REFERENCES documents,
  tenant_id text REFERENCES tenants,
  text text NOT NULL,
  match_text text NOT NULL,
  char_start integer NOT NULL,
  char_end integer NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  embedding vector(1024)
);
CREATE INDEX chunks_scope ON chunks (strategy, tenant_id);

CREATE TABLE disputes (
  tenant_id text NOT NULL REFERENCES tenants,
  dispute_id text NOT NULL,
  account_ref text NOT NULL,
  text text NOT NULL,
  status text NOT NULL DEFAULT 'open',
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, dispute_id),
  FOREIGN KEY (tenant_id, account_ref) REFERENCES accounts
);

CREATE TABLE review_items (
  tenant_id text NOT NULL,
  review_id text NOT NULL,
  dispute_id text NOT NULL,
  queue text NOT NULL CHECK (queue IN ('human', 'fraud')),
  reasons jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved')),
  resolution jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, review_id),
  UNIQUE (tenant_id, dispute_id, queue),
  FOREIGN KEY (tenant_id, dispute_id) REFERENCES disputes
);

-- status 'issued' is seeded history. The app can only ever write 'draft' (enforced in 0002).
CREATE TABLE credit_notes (
  tenant_id text NOT NULL,
  credit_note_id text NOT NULL,
  account_ref text NOT NULL,
  dispute_id text,
  idempotency_key text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft', 'issued')),
  rule_id text,
  rule_version integer,
  credit_cents bigint NOT NULL CHECK (credit_cents >= 0),
  computation jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, credit_note_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, account_ref) REFERENCES accounts
);

CREATE TABLE credit_note_lines (
  tenant_id text NOT NULL,
  credit_note_id text NOT NULL,
  line_id text NOT NULL,
  amount_cents bigint NOT NULL CHECK (amount_cents >= 0),
  PRIMARY KEY (tenant_id, credit_note_id, line_id),
  FOREIGN KEY (tenant_id, credit_note_id) REFERENCES credit_notes,
  FOREIGN KEY (tenant_id, line_id) REFERENCES invoice_lines (tenant_id, line_id)
);
