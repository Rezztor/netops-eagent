-- Retail list prices from prices.azure.com, one row per meter asked for, kept seven days so the advice and the
-- cost estimate do not ask Microsoft on every call (plugins/_ea_azure/helpers/prices.py is the only writer).
--
-- Instance-wide with no row-level security and no foreign key: it is public data, not the tenant's. The
-- application role gets its grants from the default privileges set in 0001. Phase 2's migration must not
-- create this table again (it takes the next free number and leaves this one alone).

CREATE TABLE cloud_price (
    key        TEXT PRIMARY KEY,                 -- "<region>|<service>|<sku>|<meter>"; sku is the caller's skuName, "" meter = none asked for
    usd_hour   NUMERIC,                          -- set when Microsoft prices the meter per hour
    usd_unit   NUMERIC,                          -- set otherwise: per month, per GB, ...
    unit       TEXT NOT NULL DEFAULT '',         -- Microsoft's unitOfMeasure, e.g. "1 Hour", "1/Month"
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
