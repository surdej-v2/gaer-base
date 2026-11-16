-- 0032 (developer) redefined the allowed AI surfaces without 'tas' from 0024 (the TAS simulator), so TAS answers
-- failed to be counted. Both surfaces are allowed again.
alter table ai_usage drop constraint if exists ai_usage_surface_check;
alter table ai_usage add constraint ai_usage_surface_check
    check (surface in ('app', 'extension', 'word', 'outlook', 'document', 'mobile', 'knowledge', 'developer', 'tas'));
