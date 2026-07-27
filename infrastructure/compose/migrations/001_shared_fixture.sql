BEGIN;

CREATE SCHEMA IF NOT EXISTS workbench_shared;

CREATE TABLE IF NOT EXISTS workbench_shared.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workbench_shared.workspaces (
    workspace_id text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workbench_shared.actors (
    actor_id text PRIMARY KEY,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workbench_shared.projects (
    project_id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES workbench_shared.workspaces(workspace_id),
    name text NOT NULL,
    created_by text REFERENCES workbench_shared.actors(actor_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workbench_shared.candidate_revisions (
    project_id text NOT NULL REFERENCES workbench_shared.projects(project_id),
    candidate_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    payload jsonb NOT NULL,
    created_by text REFERENCES workbench_shared.actors(actor_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, candidate_id, revision)
);

CREATE TABLE IF NOT EXISTS workbench_shared.activity_runs (
    run_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES workbench_shared.projects(project_id),
    candidate_id text NOT NULL,
    candidate_revision integer NOT NULL,
    activity_id text NOT NULL,
    payload jsonb NOT NULL,
    created_by text REFERENCES workbench_shared.actors(actor_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, candidate_id, candidate_revision)
        REFERENCES workbench_shared.candidate_revisions(project_id, candidate_id, revision)
);

CREATE TABLE IF NOT EXISTS workbench_shared.review_runs (
    review_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES workbench_shared.projects(project_id),
    activity_run_id text REFERENCES workbench_shared.activity_runs(run_id),
    payload jsonb NOT NULL,
    created_by text REFERENCES workbench_shared.actors(actor_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workbench_shared.artifact_references (
    artifact_id text PRIMARY KEY,
    project_id text REFERENCES workbench_shared.projects(project_id),
    object_key text NOT NULL UNIQUE,
    content_digest text NOT NULL CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_digest, size_bytes)
);

INSERT INTO workbench_shared.schema_migrations(version)
VALUES ('001_shared_fixture')
ON CONFLICT (version) DO NOTHING;

COMMIT;
