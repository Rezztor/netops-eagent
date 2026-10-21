-- What Cost Management last answered, kept so a question asked again within four hours is answered from here and
-- not from Azure (plugins/_ea_azure/helpers/cost.py is the only reader and writer). One row per question: the key
-- is "<kind>|<scope>|<period>|<grouping>|...", the body is the answer already cut down to the fields the plugin
-- shows (cost rows, budgets, alert fields), never Azure's raw reply. Rows older than seven days are deleted by the
-- next write; a row older than four hours is served only when Azure cannot be asked, and says how old it is.
--
-- Instance-wide with no row-level security and no foreign key, like cloud_price (0018): access is RBAC on the
-- endpoint and the tool. The application role gets its grants from the default privileges set in 0001.

CREATE TABLE cloud_cost_cache (
    key        TEXT PRIMARY KEY,
    body       JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
