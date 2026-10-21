-- Azure drift report (ADR 0072): an engineer's acceptance of what changed in Azure outside the product.
-- `accepted_at` is the newest change line (cloud_change.at) the engineer saw and accepted for this resource; a line
-- after it is drift again. Nothing here changes Azure. Instance-wide, no row-level security, like cloud_managed.
ALTER TABLE cloud_managed ADD COLUMN accepted_at TIMESTAMPTZ;
