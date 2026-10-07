-- One-time codes get a purpose, so a code sent to confirm a new phone number cannot be used to log in
-- (and the other way round).
alter table otp_codes add column purpose text not null default 'login' check (purpose in ('login', 'phone_change'));
alter table otp_codes add column user_id bigint references users (id) on delete cascade;   -- phone_change: who asked
drop index otp_codes_phone_idx;
create index otp_codes_phone_idx on otp_codes (phone, purpose, created_at desc);
