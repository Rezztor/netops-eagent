"""Type to category and label. No database, no network."""

from plugins._ea_azure.helpers import categories as c, shape


def test_category_by_type_by_namespace_and_other():
    assert c.category_of("microsoft.web/sites") == "web"
    assert c.category_of("Microsoft.Network/virtualNetworks/subnets") == "networking"
    assert c.category_of("microsoft.network/somethingnew") == "networking"      # the family of an unlisted type
    assert c.category_of("microsoft.contoso/widgets") == "other"                  # an unknown family
    assert c.category_of("") == "other"


def test_the_table_only_uses_the_ten_categories_and_is_big_enough():
    assert list(c.CATEGORIES) == ["compute", "web", "containers", "networking", "storage", "databases",
                                  "identity_security", "integration", "monitoring", "other"]
    assert set(c.BY_TYPE.values()) <= set(c.CATEGORIES) and set(c.BY_NAMESPACE.values()) <= set(c.CATEGORIES)
    assert len(c.BY_TYPE) >= 150
    assert all(t == t.lower() for t in c.BY_TYPE)


def test_every_type_the_allow_list_reads_is_filed_where_the_screen_expects():
    assert {t for t in shape.ALLOW if not c.known(t)} == set()


def test_labels():
    assert c.label_of("microsoft.web/sites", "app,linux") == "Web app"
    assert c.label_of("microsoft.web/sites", "functionapp,linux") == "Function app"
    assert c.label_of("microsoft.web/sites", "functionapp,workflowapp") == "Logic app (standard)"
    assert c.label_of("microsoft.network/virtualnetworks") == "Virtual network"
    assert c.label_of("microsoft.contoso/widgets") == "widgets"


def test_aliases_name_types_that_exist():
    for alias, type_ in c.TYPE_ALIASES.items():
        assert c.known(type_), alias
    assert c.TYPE_ALIASES["vnet"] == c.TYPE_ALIASES["virtual network"]
