-- =============================================================
-- PlanLab database schema (Supabase / Postgres)
-- Run once in Supabase: SQL Editor > New query > paste > Run
-- Safe to re-run: every statement uses IF NOT EXISTS.
-- =============================================================


-- ---------- Core: experiments, arms, events ----------

create table if not exists experiments (
    id            text primary key,              -- same 8-char id the API uses
    goal          text not null,
    source        text not null default 'fallback',   -- gemini | fallback
    status        text not null default 'running',    -- running | stopped_arm | winner
    winner        text,
    stopped_arms  jsonb not null default '[]'::jsonb,
    guardrails    jsonb not null default '{}'::jsonb,
    rollout       jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create table if not exists arms (
    experiment_id text not null references experiments(id) on delete cascade,
    arm_id        text not null,
    name          text not null,
    price         numeric(12,2) not null check (price > 0),
    currency      text not null default 'USD',
    interval      text not null default 'MONTH' check (interval in ('MONTH', 'YEAR')),
    trial_days    int  not null default 0 check (trial_days between 0 and 90),
    setup_fee     numeric(12,2) not null default 0 check (setup_fee >= 0),
    is_baseline   boolean not null default false,
    reason        text not null default '',
    plan_id       text,                          -- PayPal plan id (P-...)
    position      int  not null default 0,       -- keeps arm order stable
    primary key (experiment_id, arm_id)
);

create table if not exists events (
    id            bigserial primary key,
    experiment_id text not null references experiments(id) on delete cascade,
    arm_id        text not null,
    subscriber_id text not null,
    type          text not null check (type in ('VISIT','SIGNUP','PAYMENT','CANCELLED','DENIED',
                                                'PAYMENT_FAILED','REFUND','DISPUTE','CANCEL_INTENT')),
    ts            timestamptz not null,
    amount        numeric(12,2) not null default 0 check (amount >= 0),
    source        text not null default 'real' check (source in ('real', 'simulated')),
    created_at    timestamptz not null default now()
);
create index if not exists events_experiment_idx on events (experiment_id, ts);
create index if not exists events_subscriber_idx on events (subscriber_id);


-- ---------- PayPal ----------

-- Which subscription belongs to which experiment arm (survives restarts)
create table if not exists paypal_subscriptions (
    subscription_id text primary key,            -- I-...
    experiment_id   text not null references experiments(id) on delete cascade,
    arm_id          text not null,
    status          text,                        -- APPROVAL_PENDING | ACTIVE | CANCELLED ...
    approve_url     text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Every webhook received, for debugging and to ignore PayPal's retries
create table if not exists paypal_webhook_log (
    webhook_id    text primary key,              -- PayPal event id (WH-...)
    event_type    text not null,
    result        jsonb,
    received_at   timestamptz not null default now()
);


-- ---------- Bryntum Gantt (experiment timeline) ----------
-- Shapes follow Bryntum's task / dependency model so the Gantt can load them directly.

create table if not exists gantt_tasks (
    id              bigserial primary key,
    experiment_id   text not null references experiments(id) on delete cascade,
    parent_id       bigint references gantt_tasks(id) on delete cascade,
    name            text not null,
    kind            text not null default 'task'
                    check (kind in ('experiment', 'launch', 'checkpoint', 'decision', 'rollout', 'task')),
    start_date      timestamptz not null,
    end_date        timestamptz,
    duration        numeric,                     -- in days; Bryntum can calculate end_date from it
    percent_done    numeric not null default 0 check (percent_done between 0 and 100),
    status          text not null default 'pending',  -- pending | passed | breached | done
    note            text,
    ordinal         int not null default 0,      -- display order among siblings
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists gantt_tasks_experiment_idx on gantt_tasks (experiment_id);

create table if not exists gantt_dependencies (
    id              bigserial primary key,
    experiment_id   text not null references experiments(id) on delete cascade,
    from_task       bigint not null references gantt_tasks(id) on delete cascade,
    to_task         bigint not null references gantt_tasks(id) on delete cascade,
    type            int not null default 2       -- Bryntum: 2 = finish-to-start
);


-- ---------- AG Grid / AG Studio (merchant workspace) ----------

-- Saved grid views: columns, filters, sorting per grid (e.g. 'arms', 'cohorts')
create table if not exists grid_views (
    id              bigserial primary key,
    experiment_id   text references experiments(id) on delete cascade,  -- null = global view
    grid_id         text not null,               -- arms | cohorts | transactions | ...
    name            text not null,
    column_state    jsonb not null default '[]'::jsonb,
    filter_model    jsonb not null default '{}'::jsonb,
    sort_model      jsonb not null default '[]'::jsonb,
    is_default      boolean not null default false,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- AG Studio dashboards built with the AI assistant (layout saved as JSON)
create table if not exists dashboards (
    id              bigserial primary key,
    experiment_id   text references experiments(id) on delete cascade,
    name            text not null,
    layout          jsonb not null default '{}'::jsonb,
    created_by      text not null default 'user',    -- user | ai
    prompt          text,                            -- the request that created it, if AI
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Daily metric snapshots so charts show history (MRR, conversion, churn per arm)
create table if not exists metric_snapshots (
    id              bigserial primary key,
    experiment_id   text not null references experiments(id) on delete cascade,
    arm_id          text not null,
    day             int not null,                -- simulated or real day number
    visitors        int not null default 0,
    signups         int not null default 0,
    churned         int not null default 0,
    mrr             numeric(12,2) not null default 0,
    conversion      numeric,
    churn           numeric,
    ltv             numeric,
    prob_best       numeric,
    captured_at     timestamptz not null default now(),
    unique (experiment_id, arm_id, day)
);


-- ---------- Security ----------
-- Our backend connects as the database owner, which bypasses these rules.
-- Turning RLS on with no policies blocks Supabase's public API from reading
-- or writing these tables with the anon key.

alter table experiments          enable row level security;
alter table arms                 enable row level security;
alter table events               enable row level security;
alter table paypal_subscriptions enable row level security;
alter table paypal_webhook_log   enable row level security;
alter table gantt_tasks          enable row level security;
alter table gantt_dependencies   enable row level security;
alter table grid_views           enable row level security;
alter table dashboards           enable row level security;
alter table metric_snapshots     enable row level security;
