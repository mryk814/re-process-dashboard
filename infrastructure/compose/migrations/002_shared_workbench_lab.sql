BEGIN;

INSERT INTO workbench_shared.workspaces(workspace_id, name)
VALUES ('shared-lab', 'Shared Workbench Lab')
ON CONFLICT (workspace_id) DO NOTHING;

ALTER TABLE workbench_shared.actors
    ADD COLUMN IF NOT EXISTS actor_kind text NOT NULL DEFAULT 'human',
    ADD COLUMN IF NOT EXISTS workspace_id text REFERENCES workbench_shared.workspaces(workspace_id),
    ADD COLUMN IF NOT EXISTS capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS label text;

UPDATE workbench_shared.actors
SET workspace_id = COALESCE(workspace_id, 'shared-lab'),
    label = COALESCE(label, display_name);

ALTER TABLE workbench_shared.actors
    ALTER COLUMN workspace_id SET NOT NULL,
    ALTER COLUMN label SET NOT NULL;

DO $$
BEGIN
    ALTER TABLE workbench_shared.actors
        ADD CONSTRAINT actors_kind_check
        CHECK (actor_kind IN ('human', 'ai_agent', 'service'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

INSERT INTO workbench_shared.actors(
    actor_id,
    display_name,
    label,
    actor_kind,
    workspace_id,
    capabilities
)
VALUES
    (
        'human-a',
        'Human A',
        'Human A',
        'human',
        'shared-lab',
        '["project:read", "project:write", "candidate:read", "candidate:write", "run:read", "run:write", "artifact:read", "artifact:write", "audit:read"]'::jsonb
    ),
    (
        'human-b',
        'Human B',
        'Human B',
        'human',
        'shared-lab',
        '["project:read", "project:write", "candidate:read", "candidate:write", "run:read", "run:write", "artifact:read", "artifact:write", "audit:read"]'::jsonb
    ),
    (
        'ai-reviewer',
        'AI Reviewer Fixture',
        'AI Reviewer Fixture',
        'ai_agent',
        'shared-lab',
        '["project:read", "candidate:read", "run:read", "artifact:read", "review:write"]'::jsonb
    ),
    (
        'service-migration',
        'Shared Lab Migration',
        'Shared Lab Migration',
        'service',
        'shared-lab',
        '[]'::jsonb
    )
ON CONFLICT (actor_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    label = EXCLUDED.label,
    actor_kind = EXCLUDED.actor_kind,
    workspace_id = EXCLUDED.workspace_id,
    capabilities = EXCLUDED.capabilities;

UPDATE workbench_shared.projects
SET created_by = 'service-migration'
WHERE created_by IS NULL;

UPDATE workbench_shared.candidate_revisions
SET created_by = 'service-migration'
WHERE created_by IS NULL;

UPDATE workbench_shared.activity_runs
SET created_by = 'service-migration'
WHERE created_by IS NULL;

UPDATE workbench_shared.review_runs
SET created_by = 'service-migration'
WHERE created_by IS NULL;

ALTER TABLE workbench_shared.projects
    ALTER COLUMN created_by SET NOT NULL;

ALTER TABLE workbench_shared.candidate_revisions
    ADD COLUMN IF NOT EXISTS name text;

UPDATE workbench_shared.candidate_revisions
SET name = COALESCE(name, payload->>'name', candidate_id)
WHERE name IS NULL;

ALTER TABLE workbench_shared.candidate_revisions
    ALTER COLUMN created_by SET NOT NULL,
    ALTER COLUMN name SET NOT NULL;

ALTER TABLE workbench_shared.activity_runs
    ALTER COLUMN created_by SET NOT NULL;

ALTER TABLE workbench_shared.review_runs
    ALTER COLUMN created_by SET NOT NULL;

CREATE TABLE IF NOT EXISTS workbench_shared.candidates (
    project_id text NOT NULL REFERENCES workbench_shared.projects(project_id),
    candidate_id text NOT NULL,
    current_revision integer NOT NULL CHECK (current_revision > 0),
    name text NOT NULL,
    created_by text NOT NULL REFERENCES workbench_shared.actors(actor_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, candidate_id),
    FOREIGN KEY (project_id, candidate_id, current_revision)
        REFERENCES workbench_shared.candidate_revisions(project_id, candidate_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS workbench_shared.audit_events (
    event_id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES workbench_shared.workspaces(workspace_id),
    project_id text REFERENCES workbench_shared.projects(project_id),
    target_type text NOT NULL,
    target_id text NOT NULL,
    actor_id text NOT NULL REFERENCES workbench_shared.actors(actor_id),
    operation text NOT NULL,
    outcome text NOT NULL,
    expected_revision integer,
    resulting_revision integer,
    request_id text NOT NULL,
    correlation_id text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE workbench_shared.artifact_references
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS created_by text REFERENCES workbench_shared.actors(actor_id),
    ADD COLUMN IF NOT EXISTS verified_at timestamptz;

UPDATE workbench_shared.artifact_references
SET status = 'pending',
    created_by = COALESCE(created_by, 'service-migration'),
    verified_at = NULL
WHERE created_by IS NULL OR verified_at IS NULL;

ALTER TABLE workbench_shared.artifact_references
    ALTER COLUMN created_by SET NOT NULL;

DO $$
BEGIN
    ALTER TABLE workbench_shared.artifact_references
        ADD CONSTRAINT artifact_status_check
        CHECK (
            status IN ('pending', 'ready', 'failed')
            AND (
                status <> 'ready'
                OR (verified_at IS NOT NULL AND created_by IS NOT NULL)
            )
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'workbench_shared.activity_runs'::regclass
          AND conname = 'activity_runs_project_run_unique'
    ) THEN
        ALTER TABLE workbench_shared.activity_runs
            ADD CONSTRAINT activity_runs_project_run_unique
            UNIQUE (project_id, run_id);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'workbench_shared.review_runs'::regclass
          AND conname = 'review_runs_project_activity_fk'
    ) THEN
        ALTER TABLE workbench_shared.review_runs
            ADD CONSTRAINT review_runs_project_activity_fk
            FOREIGN KEY (project_id, activity_run_id)
            REFERENCES workbench_shared.activity_runs(project_id, run_id);
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION workbench_shared.enforce_actor_workspace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actor_workspace text;
    project_workspace text;
    resolved_actor text;
BEGIN
    IF TG_TABLE_NAME = 'audit_events' THEN
        resolved_actor := NEW.actor_id;
    ELSE
        resolved_actor := NEW.created_by;
    END IF;

    SELECT workspace_id INTO actor_workspace
    FROM workbench_shared.actors
    WHERE actor_id = resolved_actor;

    IF TG_TABLE_NAME IN ('audit_events', 'projects') THEN
        project_workspace := NEW.workspace_id;
    ELSE
        SELECT workspace_id INTO project_workspace
        FROM workbench_shared.projects
        WHERE project_id = NEW.project_id;
    END IF;

    IF actor_workspace IS DISTINCT FROM project_workspace THEN
        RAISE EXCEPTION 'actor and project must belong to the same workspace'
            USING ERRCODE = '23514';
    END IF;

    IF TG_TABLE_NAME = 'audit_events' AND NEW.project_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM workbench_shared.projects
            WHERE project_id = NEW.project_id
              AND workspace_id = NEW.workspace_id
        ) THEN
            RAISE EXCEPTION 'audit project must belong to the event workspace'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS projects_actor_workspace ON workbench_shared.projects;
CREATE TRIGGER projects_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.projects
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS candidate_revisions_actor_workspace ON workbench_shared.candidate_revisions;
CREATE TRIGGER candidate_revisions_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.candidate_revisions
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS candidates_actor_workspace ON workbench_shared.candidates;
CREATE TRIGGER candidates_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.candidates
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS activity_runs_actor_workspace ON workbench_shared.activity_runs;
CREATE TRIGGER activity_runs_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.activity_runs
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS review_runs_actor_workspace ON workbench_shared.review_runs;
CREATE TRIGGER review_runs_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.review_runs
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS artifact_references_actor_workspace ON workbench_shared.artifact_references;
CREATE TRIGGER artifact_references_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.artifact_references
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

DROP TRIGGER IF EXISTS audit_events_actor_workspace ON workbench_shared.audit_events;
CREATE TRIGGER audit_events_actor_workspace
BEFORE INSERT OR UPDATE ON workbench_shared.audit_events
FOR EACH ROW EXECUTE FUNCTION workbench_shared.enforce_actor_workspace();

INSERT INTO workbench_shared.schema_migrations(version)
VALUES ('002_shared_workbench_lab')
ON CONFLICT (version) DO NOTHING;

COMMIT;
