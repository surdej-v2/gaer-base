-- Sign in with Apple as a login provider (app/login_providers.py, /admin/login-providers): one more kind.
--
-- Apple differs from the other OpenID Connect providers only in how it talks: no PKCE (state + nonce bind the login),
-- its answer is a form the browser posts to the callback, and instead of a fixed client secret every token call carries
-- a short-lived JWT signed with the team's private key (.p8). That key is what client_secret_enc holds for an Apple
-- row — encrypted with app.secrets_box like the other secrets, never returned. The rules after the provider are the
-- same as for every other kind: it replaces only the one-time code.

alter table login_providers drop constraint login_providers_kind_check;
alter table login_providers add constraint login_providers_kind_check
    check (kind in ('entra', 'google', 'apple', 'github', 'amazon', 'saml'));
