-- AI in Gær Developer, fase 1 (specs/developer/AI.md): a short AI summary of a version waiting for approval, written
-- by the job developer.ai_summary when the version is submitted and AI is on. Metadata only (the contract changes).
alter table dev_artifact_versions add column ai_summary text check (length(ai_summary) <= 2000);
