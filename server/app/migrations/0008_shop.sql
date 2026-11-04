-- Self-service shop (/admin/shop, /w/butik): products with a QR code each. Scanning the code opens
-- /w/butik/<code>, the buyer pays with MobilePay and gets a receipt page with the order number.
-- Orders hold no personal data: no user, no phone number. The buyer is anonymous to the shop.

create table shop_products (
    id           bigserial primary key,
    code         text not null unique check (code ~ '^[a-z0-9]{6,16}$'),  -- public, printed in the QR code
    name         text not null,
    description  text not null default '',
    price_ore    integer not null check (price_ore > 0),                  -- price in øre (1 kr = 100 øre)
    stock        integer check (stock is null or stock >= 0),             -- null = unlimited
    active       boolean not null default true,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    created_by   bigint references users (id) on delete set null
);

create table shop_orders (
    id              bigserial primary key,
    token           text not null unique check (token ~ '^[a-f0-9]{32}$'),  -- receipt URL + payment reference
    product_id      bigint references shop_products (id) on delete set null,
    product_name    text not null,                                          -- snapshot at the time of sale
    quantity        integer not null check (quantity between 1 and 99),
    unit_price_ore  integer not null check (unit_price_ore > 0),
    amount_ore      integer not null check (amount_ore > 0),
    currency        text not null default 'DKK',
    provider        text not null check (provider in ('console', 'mobilepay')),
    status          text not null default 'pending' check (status in ('pending', 'paid', 'cancelled')),
    provider_state  text,                                                   -- last state seen at the provider
    created_at      timestamptz not null default now(),
    paid_at         timestamptz,
    fulfilled_at    timestamptz,
    fulfilled_by    bigint references users (id) on delete set null
);
create index shop_orders_created_idx on shop_orders (created_at desc);
create index shop_orders_status_idx on shop_orders (status, created_at desc);
create index shop_orders_product_idx on shop_orders (product_id);
