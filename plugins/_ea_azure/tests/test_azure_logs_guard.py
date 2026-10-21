"""The free-form KQL guard (helpers/logs.py `guard`): what it accepts and what it refuses. Written first, as the contract
asks; loosening the guard means extending this table before the code. No database, no network."""

import pytest

from plugins._ea_azure.helpers import logs

TABLES = ("AzureDiagnostics", "AZFWNetworkRule", "Heartbeat")


def guard(text):
    return logs.guard(text, TABLES)


ACCEPT = {
    "a table and a filter": "AZFWNetworkRule | where Action == 'Deny' | summarize count() by SourceIp",
    "several lines and a comment": "Heartbeat // who is alive\n| where Computer startswith 'vm-'\n| take 5",
    "a let for a scalar": "let ip = '10.0.0.4';\nAZFWNetworkRule | where SourceIp == ip",
    "a let for a table": "let denied = AZFWNetworkRule | where Action == 'Deny';\ndenied | summarize count() by Rule",
    "a let for a table in brackets": "let denied = (AZFWNetworkRule | where Action == 'Deny');\ndenied | count",
    "a trailing semicolon": "Heartbeat | count;",
    "a join of two allowed tables": "AZFWNetworkRule | join kind=inner (Heartbeat | project Computer) on $left.SourceIp == $right.Computer",
    "a union of two allowed tables": "union AZFWNetworkRule, Heartbeat | count",
    "a union with options": "union kind=outer withsource=T AZFWNetworkRule, Heartbeat | count",
    "a subquery with toscalar": "Heartbeat | where TimeGenerated > toscalar(Heartbeat | summarize max(TimeGenerated))",
    "a take larger than the cap": "Heartbeat | take 100000",
    "a bag indexed by a string": "AzureDiagnostics | extend m = parse_json(properties_s)['message'] | take 3",
    "a string holding a refused word": "Heartbeat | where Computer == 'externaldata arg( .show database(x)'",
    "a string holding a quote and a comment mark": "Heartbeat | where Computer == 'it\\'s // not a comment'",
    "a verbatim string": "Heartbeat | where Computer == @'C:\\temp\\x'",
    "a refused word as part of a longer name": "Heartbeat | extend app_count = 1, searching = 2, invoked = 3",
    "functions of a column": "Heartbeat | where isnotempty(Computer) | summarize dcount(Computer), max(TimeGenerated) by bin(TimeGenerated, 1h)",
    "a column in brackets in a filter": "Heartbeat | where (Computer) == 'vm-1' and not (Computer == 'vm-2')",
    "an allowed table alone in brackets after join": "AZFWNetworkRule | join kind=inner (Heartbeat) on Computer",
    "a let table alone in brackets": "let alive = Heartbeat | take 5;\nAZFWNetworkRule | where SourceIp in (alive) | join (alive) on Computer",
}

REFUSE = {
    "empty": ("", "nothing to run"),
    "only a comment": ("// hi", "nothing to run"),
    "a table that is not allowed": ("SigninLogs | take 5", "SigninLogs"),
    "a table with other letter case": ("heartbeat | take 5", "heartbeat"),
    "a table in brackets that is not allowed": ("['SigninLogs'] | take 5", "SigninLogs"),
    "a dot command": (".show tables", "dot"),
    "a dot command after a table": ("Heartbeat | take 1\n.drop table Heartbeat", "dot"),
    "a dot command after a let": ("let a = 1;\n.set Heartbeat <| print 1", "dot"),
    "externaldata": ("externaldata (x:string) ['https://evil.example/x.csv'] | take 1", "externaldata"),
    "external_table": ("external_table('t') | take 1", "external_table"),
    "adx": ("adx('https://c.kusto.windows.net/db').T | take 1", "adx"),
    "arg": ("arg('').Resources | take 1", "arg"),
    "workspace": ("workspace('00000000-0000-0000-0000-000000000000').Heartbeat | take 1", "workspace"),
    "workspace with a space": ("workspace ('x').Heartbeat | take 1", "workspace"),
    "app": ("app('x').requests | take 1", "app"),
    "resource": ("resource('/subscriptions/x').Heartbeat | take 1", "resource"),
    "cluster": ("cluster('x').database('y').T | take 1", "cluster"),
    "database": ("database('y').T | take 1", "database"),
    "table by name": ("table('SecurityEvent') | take 1", "table"),
    "a materialized view": ("materialized-view('x') | take 1", "materialized"),
    "evaluate": ("Heartbeat | evaluate ipv4_lookup(Heartbeat, Computer, Computer)", "evaluate"),
    "invoke": ("Heartbeat | invoke f()", "invoke"),
    "find across tables": ("find in (SigninLogs) where true", "find"),
    "search across tables": ("search 'password'", "search"),
    "a union of everything": ("union * | take 1", "union"),
    "a union with a wildcard": ("union AZ* | take 1", "union"),
    "a union with a table that is not allowed": ("union Heartbeat, SecurityEvent | count", "SecurityEvent"),
    "a join with a table that is not allowed": ("Heartbeat | join SecurityEvent on Computer", "SecurityEvent"),
    "a join with a subquery on a table that is not allowed": ("Heartbeat | join (SecurityEvent | take 1) on Computer", "SecurityEvent"),
    "a lookup of a table that is not allowed": ("Heartbeat | lookup SecurityEvent on Computer", "SecurityEvent"),
    "a let that names a table that is not allowed": ("let t = SecurityEvent;\nt | take 1", "SecurityEvent"),
    "a subquery in a scalar": ("Heartbeat | where Computer in (SecurityEvent | project Computer)", "SecurityEvent"),
    "a table in a materialize": ("Heartbeat | join (materialize(SecurityEvent | take 1)) on Computer", "SecurityEvent"),
    "a bare table in in()": ("Heartbeat | where Computer in (SecurityEvent)", "SecurityEvent"),
    # A table alone in brackets, with no `|` after it, wherever a query can stand.
    "a bare table in brackets after join": ("Heartbeat | join (SecurityEvent) on Computer", "SecurityEvent"),
    "the same after join options": ("Heartbeat | join kind=inner (SecurityEvent) on Computer", "SecurityEvent"),
    "the same in double brackets": ("Heartbeat | join ((SecurityEvent)) on Computer", "SecurityEvent"),
    "the same with a quoted name": ("Heartbeat | join (['SecurityEvent']) on Computer", "SecurityEvent"),
    "a bare table in brackets after lookup": ("Heartbeat | lookup (SecurityEvent) on Computer", "SecurityEvent"),
    "a bare table in brackets in a union": ("Heartbeat | union (SecurityEvent)", "SecurityEvent"),
    "the same after an allowed table": ("union Heartbeat, (SecurityEvent) | count", "SecurityEvent"),
    "the same after union options": ("union withsource=T (SecurityEvent) | count", "SecurityEvent"),
    "a bare table in in(()))": ("Heartbeat | where Computer in ((SecurityEvent))", "SecurityEvent"),
    "a bare table in in~()": ("Heartbeat | where Computer in~ (SecurityEvent)", "SecurityEvent"),
    "a bare table in has_any()": ("Heartbeat | where Computer has_any (SecurityEvent)", "SecurityEvent"),
    "a bare table in toscalar()": ("Heartbeat | extend x = toscalar(SecurityEvent)", "SecurityEvent"),
    "a bare table in materialize()": ("Heartbeat | join (materialize(SecurityEvent)) on Computer", "SecurityEvent"),
    "a bare table after mv-apply on": ("Heartbeat | mv-apply x = Computer on (SecurityEvent)", "SecurityEvent"),
    "a bare table in a fork": ("Heartbeat | fork (SecurityEvent)", "SecurityEvent"),
    "a bare table in a facet": ("Heartbeat | facet by Computer with (SecurityEvent)", "SecurityEvent"),
    "a partition": ("Heartbeat | partition by Computer (SecurityEvent)", "partition"),
    "a function body": ("let f = () { SecurityEvent | take 1 };\nf() | take 1", "brace"),
    "a statement that is not a let": ("set notruncation;\nHeartbeat | take 1", "let"),
    "a second query": ("Heartbeat | take 1;\nAZFWNetworkRule | take 1", "let"),
    "a query that starts with print": ("print 1", "`print`"),
    "an unterminated string": ("Heartbeat | where Computer == 'x", "string"),
    "a string over a newline": ("Heartbeat | where Computer == 'x\n| take 1'", "string"),
    "a character outside ASCII": ("Heartbeat | where Computer == ｘ", "ASCII"),
    "a control character": ("Heartbeat\x00 | take 1", "control"),
    "an identity column by name": ("Heartbeat | extend c = Claims", "Claims"),
    "an identity column in other case": ("Heartbeat | project calleripaddress", "calleripaddress"),
    # The same columns under a name the answer's filter does not know: it drops a column by what it is called.
    "an identity column in quoted brackets": ("AzureDiagnostics | extend c = ['CallerIpAddress']", "CallerIpAddress"),
    "an identity column in double-quoted brackets": ('AzureDiagnostics | project c = ["Claims"]', "Claims"),
    "an identity column written in two pieces": ("AzureDiagnostics | extend c = ['Caller' 'IpAddress']", "one string"),
    "a column name with an escape in it": ("AzureDiagnostics | extend c = ['Caller\\x49pAddress']", "backslash"),
    "a column named by a string": ("AzureDiagnostics | extend c = column_ifexists('CallerIpAddress', '')", "column_ifexists"),
    "a column named by a string, the old spelling": ("AzureDiagnostics | extend c = columnifexists('UserAgent', '')", "columnifexists"),
    "every column in one bag": ("AzureDiagnostics | project everything = pack_all()", "pack_all"),
    "every column in one bag, as text": ("AzureDiagnostics | project everything = tostring(pack_all(true))", "pack_all"),
    "a query that is too long": ("Heartbeat | where Computer == '" + "x" * logs.MAX_KQL + "'", "long"),
}


@pytest.mark.parametrize("why", ACCEPT)
def test_accepted(why):
    final, refusal = guard(ACCEPT[why])
    assert refusal == "", refusal
    assert final.endswith(logs.TAKE) and final.count(logs.TAKE) == 1


@pytest.mark.parametrize("why", REFUSE)
def test_refused(why):
    text, fragment = REFUSE[why]
    final, refusal = guard(text)
    assert final == "" and fragment.lower() in refusal.lower(), refusal


def test_the_cap_line_is_added_once_and_comments_do_not_reach_azure():
    final, _ = guard("Heartbeat // a comment\n| take 5 // another")
    assert final == "Heartbeat \n| take 5" + logs.TAKE
    again, _ = guard(final)
    assert again == final


def test_a_comment_cannot_hide_the_cap_line():
    final, _ = guard("Heartbeat | take 5 // x")
    assert final.endswith("\n| take 201") and "// x" not in final


def test_a_string_is_never_rewritten():
    final, _ = guard("Heartbeat | where Computer == 'a // b' and Computer != \"c'd\"")
    assert "'a // b'" in final and "\"c'd\"" in final


def test_no_allowed_tables_means_nothing_can_be_queried():
    assert logs.guard("Heartbeat | take 1", ())[0] == ""
