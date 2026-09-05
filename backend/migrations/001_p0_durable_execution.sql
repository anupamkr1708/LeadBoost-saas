-- LeadBoost SaaS
-- P0 durable execution / pipeline state / AI provenance migration
--
-- Purpose:
--   Upgrade an existing PostgreSQL schema to the P0 ORM schema.
--
-- Characteristics:
--   * Transactional
--   * Additive
--   * Idempotent for the supported pre-P0 schema
--   * Uses the same sequence-based PK representation as SQLAlchemy
--
-- IMPORTANT:
--   This migration targets existing PostgreSQL databases.
--   Fresh databases may still be initialized with:
--       Base.metadata.create_all()
--
-- Supported starting point:
--   The jobs table is absent (this is the pre-P0 schema).
--
-- Safety:
--   If a jobs table already exists, we validate that it is structurally
--   compatible rather than silently accepting a potentially partial schema.

BEGIN;

SET lock_timeout = '10s';
SET statement_timeout = '60s';

-- ============================================================
-- 1. Durable jobs table
-- ============================================================

DO $$
DECLARE
    jobs_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'jobs'
    )
    INTO jobs_exists;

    IF jobs_exists THEN
        -- The P0 migration should normally encounter no jobs table.
        -- If one already exists, make sure it has the complete durable
        -- execution shape rather than silently accepting a partial table.

        IF EXISTS (
            SELECT 1
            FROM (
                VALUES
                    ('id'),
                    ('organization_id'),
                    ('lead_id'),
                    ('job_type'),
                    ('status'),
                    ('pipeline_id'),
                    ('attempt_count'),
                    ('max_attempts'),
                    ('available_at'),
                    ('claimed_at'),
                    ('lease_expires_at'),
                    ('worker_id'),
                    ('started_at'),
                    ('completed_at'),
                    ('last_error'),
                    ('last_error_category'),
                    ('created_at'),
                    ('updated_at')
            ) AS required(column_name)
            WHERE NOT EXISTS (
                SELECT 1
                FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = 'jobs'
                  AND c.column_name = required.column_name
            )
        ) THEN
            RAISE EXCEPTION
                'P0 migration cannot continue: existing public.jobs table is missing one or more required columns';
        END IF;

        RAISE NOTICE
            'public.jobs already exists and contains all required columns; preserving existing table';
    ELSE
        -- Match SQLAlchemy's current PostgreSQL representation:
        -- integer PK + explicit sequence + nextval() default.

        CREATE SEQUENCE jobs_id_seq
            AS INTEGER
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1;

        CREATE TABLE jobs (
            id INTEGER NOT NULL DEFAULT nextval('jobs_id_seq'::regclass),
            organization_id INTEGER NOT NULL,
            lead_id INTEGER NOT NULL,
            job_type VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'PENDING',
            pipeline_id VARCHAR NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            claimed_at TIMESTAMP WITH TIME ZONE NULL,
            lease_expires_at TIMESTAMP WITH TIME ZONE NULL,
            worker_id VARCHAR NULL,
            started_at TIMESTAMP WITH TIME ZONE NULL,
            completed_at TIMESTAMP WITH TIME ZONE NULL,
            last_error TEXT NULL,
            last_error_category VARCHAR NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NULL,

            CONSTRAINT jobs_pkey
                PRIMARY KEY (id),

            CONSTRAINT jobs_organization_id_fkey
                FOREIGN KEY (organization_id)
                REFERENCES organizations(id),

            CONSTRAINT jobs_lead_id_fkey
                FOREIGN KEY (lead_id)
                REFERENCES leads(id)
        );

        ALTER SEQUENCE jobs_id_seq
            OWNED BY jobs.id;

        RAISE NOTICE
            'Created public.jobs using sequence-backed primary key';
    END IF;
END
$$;

-- ============================================================
-- 2. Durable jobs indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS ix_jobs_id
    ON jobs(id);

CREATE INDEX IF NOT EXISTS ix_jobs_organization_id
    ON jobs(organization_id);

CREATE INDEX IF NOT EXISTS ix_jobs_lead_id
    ON jobs(lead_id);

CREATE INDEX IF NOT EXISTS ix_jobs_job_type
    ON jobs(job_type);

CREATE INDEX IF NOT EXISTS ix_jobs_status
    ON jobs(status);

CREATE INDEX IF NOT EXISTS ix_jobs_pipeline_id
    ON jobs(pipeline_id);

-- ============================================================
-- 3. Stage execution correlation
-- ============================================================

ALTER TABLE scraping_logs
    ADD COLUMN IF NOT EXISTS pipeline_id VARCHAR;

ALTER TABLE scraping_logs
    ADD COLUMN IF NOT EXISTS organization_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_scraping_logs_pipeline_id
    ON scraping_logs(pipeline_id);

CREATE INDEX IF NOT EXISTS ix_scraping_logs_organization_id
    ON scraping_logs(organization_id);


ALTER TABLE lead_enrichment_logs
    ADD COLUMN IF NOT EXISTS pipeline_id VARCHAR;

ALTER TABLE lead_enrichment_logs
    ADD COLUMN IF NOT EXISTS organization_id INTEGER;

ALTER TABLE lead_enrichment_logs
    ADD COLUMN IF NOT EXISTS success BOOLEAN DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS ix_lead_enrichment_logs_pipeline_id
    ON lead_enrichment_logs(pipeline_id);

CREATE INDEX IF NOT EXISTS ix_lead_enrichment_logs_organization_id
    ON lead_enrichment_logs(organization_id);

-- ============================================================
-- 4. AI provenance
-- ============================================================

ALTER TABLE ai_decision_logs
    ADD COLUMN IF NOT EXISTS pipeline_id VARCHAR;

ALTER TABLE ai_decision_logs
    ADD COLUMN IF NOT EXISTS source VARCHAR;

ALTER TABLE ai_decision_logs
    ADD COLUMN IF NOT EXISTS evaluation_version VARCHAR;

CREATE INDEX IF NOT EXISTS ix_ai_decision_logs_pipeline_id
    ON ai_decision_logs(pipeline_id);

-- ============================================================
-- 5. Pipeline execution state
-- ============================================================

ALTER TABLE pipeline_execution_logs
    ADD COLUMN IF NOT EXISTS error_message TEXT;

-- ============================================================
-- 6. Evaluation provenance
-- ============================================================

ALTER TABLE evaluation_report_logs
    ADD COLUMN IF NOT EXISTS evaluation_version VARCHAR;

-- ============================================================
-- 7. Prompt/model provenance
-- ============================================================

ALTER TABLE prompt_execution_logs
    ADD COLUMN IF NOT EXISTS model VARCHAR;

COMMIT;
