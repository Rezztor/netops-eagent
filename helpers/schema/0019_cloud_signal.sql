-- What Azure itself says about a resource, read in the same pull as the inventory: Advisor recommendations, policy
-- non-compliance, the 14-day change records (who changed what) and Resource Health.
--
-- A table of its own rather than JSON on cloud_resource.props: props is compared key by key on every pull, so a
-- recommendation or a health word that moves would write a "changed" row into cloud_change and light the Changed tile
-- for something nobody changed. These rows are replaced, not diffed: a pull makes one subscription's rows of one kind
-- match one complete read. A row can name a resource the plugin does not store (a subscription, a resource in a
-- subscription that is not read), so the target is a lower-cased resource id, not a foreign key. A kind that was read
-- and found nothing leaves one marker row with target_native_id = '', so "Azure reported nothing" can be told from
-- "never read".
--
-- Instance-wide with no row-level security, like 0016 (access is RBAC on the endpoints). The foreign key is
-- DEFERRABLE INITIALLY IMMEDIATE for the backup restore, which loads tables in name order.

CREATE TABLE cloud_signal (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id  UUID NOT NULL REFERENCES cloud_container (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    kind             TEXT NOT NULL CHECK (kind IN ('advisor','policy','change','health')),
    target_native_id TEXT NOT NULL,                -- the resource the row is about, lower-cased
    at               TIMESTAMPTZ,                  -- when Azure says it happened or was assessed
    data             JSONB NOT NULL DEFAULT '{}',  -- allow-listed fields only (shape.py SIGNALS)
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cloud_signal_target ON cloud_signal (target_native_id, kind);
CREATE INDEX cloud_signal_sub ON cloud_signal (subscription_id, kind);
