-- Live migration: add the deterministic action-verb annotation columns.
-- Run once against an existing digitallibrary schema:
--   psql "$DATABASE_URL" -f sql/migrations/004_action_verbs.sql
-- The from-scratch definitions live in sql/schema/fulltext_tables.sql (keep in sync).
--
-- Idempotent: every column uses ADD COLUMN IF NOT EXISTS and every index
-- CREATE INDEX IF NOT EXISTS, so re-applying is a no-op.
--
-- Additive only — no changes to existing 003 columns. These columns flatten the
-- nested `action` object produced by python/fulltext_verbs.extract_action (the
-- deterministic action-verb parser) onto each semantic element. They are written
-- by the loader (fulltext_parse.py --to-db, parser_version 'sem-v2') and are
-- populated ONLY on rows with paragraph_type IN ('operative','preambular') — i.e.
-- resolution-body clauses. Every other row (PRST/statement bodies, annexes,
-- headings, frontmatter, votes, and every paragraph_type IS NULL element) keeps
-- all action_* / assignee_* columns NULL. Nullable throughout: a non-annotated
-- clause (a noun-phrase budget sub-item, a chapeau-less continuation) also stays
-- NULL even inside the resolution body.

BEGIN;

-- Verb head (the illocutionary act of the clause).
ALTER TABLE digitallibrary.document_paragraphs
  ADD COLUMN IF NOT EXISTS action_verb            TEXT,     -- verbatim leading surface form ('Requests', 'Also decides')
  ADD COLUMN IF NOT EXISTS action_verb_normalized TEXT,     -- legacy-compatible lemma ('request', 'take note', 'call upon')
  ADD COLUMN IF NOT EXISTS action_category        TEXT,     -- observing|reinforcing|evaluative|deciding|directive
  ADD COLUMN IF NOT EXISTS action_force           SMALLINT, -- 0-5 ordinal on the directive/deciding spine
  ADD COLUMN IF NOT EXISTS action_sentiment       SMALLINT, -- +1 / 0 / -1
  ADD COLUMN IF NOT EXISTS action_bindingness     TEXT,     -- binding|hortatory|contextual
  ADD COLUMN IF NOT EXISTS action_budget_relevant BOOLEAN,  -- clause type carries budgetary implications
  ADD COLUMN IF NOT EXISTS action_modifiers       JSONB,    -- [{kind,text}] leading adverbs/connectives/qualifiers stripped off the head
  -- Addressee (directive verbs that govern an assignee).
  ADD COLUMN IF NOT EXISTS assignee               TEXT,     -- verbatim span between the verb and the ' to '-infinitive
  ADD COLUMN IF NOT EXISTS assignee_head_noun      TEXT,     -- head noun of the assignee span (articles/quantifiers stripped)
  ADD COLUMN IF NOT EXISTS assignee_class          TEXT,     -- secretary-general|special_procedure|secretariat_entity|member_states|un_system|un_body|ngo_other|unclear
  -- Provenance / structural flags.
  ADD COLUMN IF NOT EXISTS action_inherited        BOOLEAN,  -- true when a sub-item inherits its chapeau's governing verb
  ADD COLUMN IF NOT EXISTS action_context_marker   TEXT;     -- 'chapter_vii' for 'Acting under Chapter VII ...' (normalized verb is NULL)

-- Partial indexes: only annotated rows carry these, so index just those.
CREATE INDEX IF NOT EXISTS idx_document_paragraphs_action_verb
  ON digitallibrary.document_paragraphs (action_verb_normalized)
  WHERE action_verb_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_document_paragraphs_assignee_class
  ON digitallibrary.document_paragraphs (assignee_class)
  WHERE assignee_class IS NOT NULL;

COMMIT;
