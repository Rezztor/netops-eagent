"""Every query this plugin can send to Azure Resource Graph. Constants only.

No function anywhere formats a value into query text; scope is the `subscriptions` field of the request body.
`client.graph` is only ever called with one of these. A test greps every call site.
"""

CONTAINERS = (
    "resourcecontainers | where type in~ ('microsoft.resources/subscriptions',"
    "'microsoft.resources/subscriptions/resourcegroups') "
    "| project id, name, type, subscriptionId, location, tags, properties | order by id asc"
)
RESOURCES = (
    "resources | project id, name, type, kind, location, resourceGroup, subscriptionId, sku, tags, "
    "managedBy, zones, properties | order by id asc"
)
# unverified: the table name and casing; it is the only source of an app's runtime (phase 4 checks it)
SITE_CONFIG = (
    "appserviceresources | where type =~ 'microsoft.web/sites/config' "
    "| project id, name, type, subscriptionId, resourceGroup, properties | order by id asc"
)
COUNT = "resources | summarize total = count()"

# What Azure itself says, read in the same pull as RESOURCES (shape.signal keeps allow-listed fields only).
# unverified: all four table names, the type strings and the property names, until test session 1 reads them.
ADVISOR = (
    "advisorresources | where type =~ 'microsoft.advisor/recommendations' "
    "| project id, name, type, subscriptionId, properties | order by id asc"
)
POLICY = (
    "policyresources | where type =~ 'microsoft.policyinsights/policystates' "
    "and properties.complianceState =~ 'noncompliant' "
    "| project id, name, type, subscriptionId, properties | order by id asc"
)
# newest first, so a cap keeps the latest; 14 days is what Azure keeps. The sort comes before `project`: Azure
# refuses an `order by` on a column the projection dropped (seen on the real tenant, 2026-10-03).
CHANGES = (
    "resourcechanges | extend changeTime = todatetime(properties.changeAttributes.timestamp) "
    "| where changeTime > ago(14d) | order by changeTime desc | project id, name, type, subscriptionId, properties"
)
HEALTH = (
    "healthresources | where type in~ ('microsoft.resourcehealth/availabilitystatuses', "
    "'microsoft.resourcehealth/resourceannotations') "
    "| project id, name, type, subscriptionId, properties | order by id asc"
)
