"""Change document: blocks, stdlib PDF/DOCX, digests, masks, no PyMuPDF."""

from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pypdf

from plugins._ea_azure_change.helpers import docx, document, pdf

DIGEST = "3fa9c21b07d4a8e25c0b6f1d93e47a02b85d6c14f7e9032a1bc45d68e0f7a9e1"
WHEN = 1759478280.0  # 2025-10-03-ish fixed for stable names


def _record(**over):
    base = dict(
        id="7c41e2a9abcdef0123456789",
        created_at=WHEN,
        expires_at=WHEN + 14 * 86400,
        requested_by="noura",
        summary='Create payments web app <script>alert(1)</script> & more',
        playbook_name="web_app_with_vnet",
        commit="3f2a91c",
        variables={"name": "payments", "env": "prod"},
        limit="sub-guid/rg-payments-prod-uaen",
        tier="change-high",
        plan_digest=DIGEST,
        reference="",
        diff=["+ azurerm_resource_group.rg", "+ azurerm_virtual_network.vnet"],
        rollback_commands=["Remove in the portal: web app, then plan, then VNet."],
        preflight={
            "purpose": 'A place for payments <b>bold</b> & "quotes".',
            "plan_digest": DIGEST,
            "scope": {
                "subscription": "Production",
                "resource_group": "rg-payments-prod-uaen",
                "region": "UAE North",
                "organisation": "Contoso",
                "rg_new": True,
            },
            "summary": {
                "add": 2,
                "change": 0,
                "import": 0,
                "lines": ["+ azurerm_resource_group.rg", "+ azurerm_virtual_network.vnet"],
                "resources": [
                    {
                        "address": "azurerm_resource_group.rg",
                        "type": "Resource group",
                        "name": "rg-payments-prod-uaen",
                        "action": "Create",
                        "before": {},
                        "after": {"location": "uaenorth", "tags": "env, owner"},
                    },
                    {
                        "address": "azurerm_virtual_network.vnet",
                        "type": "Virtual network",
                        "name": "vnet-payments",
                        "action": "Create",
                        "before": {},
                        "after": {"address_space": "10.30.0.0/16", "secret": "(sensitive)"},
                    },
                ],
            },
            "cost": {
                "monthly_usd": 139.43,
                "priced_at": "2026-10-01",
                "items": [{"address": "azurerm_service_plan.plan", "meter": "P1v3", "usd_month": 139.43}],
                "unknown": [],
            },
            "risk_reasons": ["costs more than 20 USD a month"],
            "advice": [{"check": "naming", "detail": "ok", "as_of": "2026-10-02"}],
            "checks": [
                {"label": "Nothing is deleted, stopped or restarted", "ok": True},
                {"label": "cost ceiling", "ok": True, "detail": "139 < 250"},
            ],
            "backout": "Remove in this order, in the portal, after your own approval: the web app, the plan, the VNet.",
        },
    )
    base.update(over)
    return SimpleNamespace(**base)


def _pdf_text(data: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return " ".join(t.text or "" for t in root.findall(".//w:t", ns))


def test_filenames():
    rec = _record()
    assert document.filename(rec, "issued", "pdf", when=WHEN) == "azure-change-7c41e2a9-issued-2025-10-03.pdf"
    rec.reference = "CR-20418"
    assert document.filename(rec, "applied", "docx", when=WHEN) == (
        "azure-change-7c41e2a9-applied-CR-20418-2025-10-03.docx"
    )
    assert document.filename(rec, "runbook", "pdf", when=WHEN) == "azure-runbook-7c41e2a9-2025-10-03.pdf"


def test_blocks_have_parts_and_blank_cr_on_issued():
    bl = document.blocks(_record(), "issued")
    texts = " ".join(str(b.get("text", "")) for b in bl)
    headings = [b["text"] for b in bl if b.get("type") == "heading"]
    assert "1 Purpose" in headings
    assert "10 Approval" in headings
    assert "ISSUED FOR APPROVAL" in texts
    assert DIGEST in texts or any(DIGEST in str(b.get("text", "")) for b in bl)
    approval = next(b for b in bl if b.get("type") == "table" and b.get("headers") and "CR" in b["headers"][0])
    assert approval["rows"][0][0].strip() == ""
    # Resource lines present; masked secret stays masked.
    joined = str(bl)
    assert "rg-payments-prod-uaen" in joined
    assert "vnet-payments" in joined
    assert "(sensitive)" in joined
    assert "super-secret-value" not in joined


def test_applied_fills_reference_and_part_11():
    rec = _record(reference="CR-20418", approved_by="noura", approved_at=WHEN + 100)
    bl = document.blocks(rec, "applied", {
        "started_at": WHEN + 200,
        "ended_at": WHEN + 300,
        "verified": True,
        "inventory_pulled_at": WHEN + 400,
        "changed_ids": ["/subscriptions/x/resourceGroups/rg-payments-prod-uaen"],
    })
    texts = " ".join(str(b) for b in bl)
    assert "AS APPLIED" in texts
    assert "CR-20418" in texts
    assert "11 As applied" in texts
    assert "rg-payments-prod-uaen" in texts


def test_pdf_roundtrip_digest_resources_and_entities(tmp_path: Path):
    rec = _record()
    files = document.build(rec, "issued", generated_at=WHEN)
    pdf_name = next(k for k in files if k.endswith(".pdf"))
    data = files[pdf_name]
    assert data.startswith(b"%PDF-1.4")
    text = _pdf_text(data)
    assert DIGEST in text
    assert "rg-payments-prod-uaen" in text
    assert "vnet-payments" in text
    assert "(sensitive)" in text
    # Angle brackets and ampersand come out as text, not markup structure.
    assert "<script>" in text or "script" in text
    assert "&" in text or "quotes" in text
    reader = pypdf.PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 1


def test_pdf_table_breaks_with_repeated_header():
    rows = [[str(i), f"row-{i}", "Create", "none", f"after-{i}"] for i in range(1, 60)]
    data = pdf.render([
        {"type": "heading", "level": 1, "text": "Table break"},
        {"type": "table", "headers": ["#", "Resource", "Action", "Before", "After"], "rows": rows},
    ], footer_left="doc · digest abcd", generated="test")
    reader = pypdf.PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 2
    # Header word appears on more than one page.
    hits = sum(1 for p in reader.pages if "Resource" in (p.extract_text() or ""))
    assert hits >= 2


def test_docx_roundtrip_xml_and_strings():
    rec = _record()
    files = document.build(rec, "issued", generated_at=WHEN)
    docx_name = next(k for k in files if k.endswith(".docx"))
    data = files[docx_name]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        assert "word/styles.xml" in names
        xml = zf.read("word/document.xml")
    ET.fromstring(xml)  # well-formed
    text = _docx_text(data)
    assert DIGEST in text
    assert "rg-payments-prod-uaen" in text
    assert "(sensitive)" in text
    assert "<script>" in text or "script" in text


def test_deterministic_text():
    rec = _record()
    a = document.blocks(rec, "issued")
    b = document.blocks(rec, "issued")
    assert a == b
    fa = document.build(rec, "issued", generated_at=WHEN)
    fb = document.build(rec, "issued", generated_at=WHEN)
    assert fa.keys() == fb.keys()
    for k in fa:
        if k.endswith(".pdf"):
            assert _pdf_text(fa[k]) == _pdf_text(fb[k])
        else:
            assert _docx_text(fa[k]) == _docx_text(fb[k])


def test_write_once_never_rewrites(tmp_path: Path):
    rec = _record()
    metas1 = document.write_once(rec, "issued", tmp_path, generated_at=WHEN)
    path = tmp_path / metas1[0]["filename"]
    first = path.read_bytes()
    path.write_bytes(b"%PDF-1.4\n%tampered\n")
    metas2 = document.write_once(rec, "issued", tmp_path, generated_at=WHEN)
    assert path.read_bytes() == b"%PDF-1.4\n%tampered\n"
    assert metas2[0]["sha256"] != metas1[0]["sha256"]
    assert first != path.read_bytes()


def test_no_pymupdf_import_in_helpers():
    root = Path(document.__file__).parent
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in {"fitz", "pymupdf"}
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in {"fitz", "pymupdf"}


def test_removal_notes_are_suggestions_not_actions():
    bl = document.blocks(_record(), "issued")
    texts = " ".join(str(b) for b in bl)
    assert "Never run by the product" in texts
    assert "removes 0" in texts


def test_free_code_is_printed_whole_in_the_document_management_reads():
    code = ['resource "azurerm_resource_group" "rg" {', '  name     = "rg-free-demo"', '  location = "uaenorth"', "}"]
    record = _record(playbook_name="free", commands=code)
    assert "rg-free-demo" in _pdf_text(document.to_pdf(document.blocks(record, "issued")))
    assert "rg-free-demo" in _docx_text(document.to_docx(document.blocks(record, "issued")))
    template = _record()
    assert not any("exactly as it will be planned" in str(b.get("text")) for b in document.blocks(template, "issued"))


# --- polish: the shape of the first real document -------------------------------------------------------------------

GUID = "368386b7-433f-463a-acd5-e188c647ce77"
LONG_BACKOUT = (
    "Only when the group is empty: confirm in the portal (Resource group, Overview) that it holds no resources and no "
    "locks, then remove the group yourself, after your own approval. The product does not remove it."
)


def _real(**over):
    rec = _record(
        requested_by="989f277a7ba94215ac58df9d845f2cd9",
        limit=f"{GUID}/rg-netops-demo",
        tier="change-low",
        summary="Create resource group for netops demo environment",
        preflight={
            "plan_digest": DIGEST,
            "scope": {"subscription": GUID, "resource_group": "rg-netops-demo", "region": "uaenorth", "rg_new": True},
            "summary": {"add": 1, "change": 0, "import": 0, "resources": [{
                "address": "azurerm_resource_group.this", "type": "azurerm_resource_group", "name": "rg-netops-demo",
                "action": "create", "before": {},
                "after": {"location": "uaenorth", "name": "rg-netops-demo",
                          "tags": {"costcentre": "netops", "env": "demo"}, "token": "(sensitive)"},
            }]},
            "cost": {"monthly_usd": 0.0, "priced_at": "2026-10-03", "items": []},
            "risk_reasons": ["tier change-low"],
            "checks": [
                {"label": "Providers", "ok": True},
                {"label": "Nothing is deleted, stopped or restarted", "ok": True},
                {"label": "Cost", "ok": True},
                {"label": "Nothing is deleted, stopped or restarted", "ok": True,
                 "detail": "every change in the plan is a create or an update"},
            ],
            "backout": LONG_BACKOUT,
        },
    )
    for k, v in over.items():
        setattr(rec, k, v)
    return rec


def _tables(bl):
    return [b for b in bl if b.get("type") == "table"]


def test_names_replace_ids_and_fall_back():
    rec = _real()
    extra = {"requested_by_name": "noura", "approved_by_name": "salim", "subscription_name": "Production"}
    text = _pdf_text(document.to_pdf(document.blocks(rec, "issued", extra)))
    assert "noura" in text and rec.requested_by not in text.replace("\n", "")
    assert f"Production [{GUID}]".replace(" ", "") in text.replace("\n", "").replace(" ", "")
    bare = " ".join(str(r) for t in _tables(document.blocks(rec, "issued")) for r in t["rows"])
    assert rec.requested_by in bare and GUID in bare and "Production" not in bare
    rec.approved_by, rec.reference, rec.approved_at = "uid-9", "CR-20418", WHEN
    applied = lambda e: " ".join(b.get("text", "") for b in document.blocks(rec, "applied", e))  # noqa: E731
    assert "Approved by salim" in applied(extra) and "Approved by uid-9" in applied({})


def test_display_names_look_up_user_and_subscription(monkeypatch):
    from helpers import users
    from plugins._ea_azure.helpers import store
    from plugins._ea_azure_change.helpers import records

    monkeypatch.setattr(users, "get_user_by_id", lambda uid: {"username": "noura"} if uid == "989f277a7ba94215ac58df9d845f2cd9" else None)
    monkeypatch.setattr(store, "subscriptions", lambda: [{"native_id": GUID, "name": "Production", "readable": True}])
    rec = _real(approved_by="gone")
    assert records.display_names(rec) == {"requested_by_name": "noura", "subscription_name": "Production"}


def test_no_question_mark_from_characters_outside_helvetica():
    bl = document.blocks(_real(), "issued")
    flat = " ".join(str(b) for b in bl)
    assert "≈" not in flat and "—" not in flat and "tier change-low" not in flat.replace("change-low: tier", "")
    for text in (_pdf_text(document.to_pdf(bl)), _docx_text(document.to_docx(bl))):
        assert "?" not in text
        assert "About 0.00 USD a month" in text.replace("\n", " ")
    assert pdf.plain("a ≈ b — c “q” … €1") == 'a ~ b - c "q" ... EUR1'
    assert pdf.plain("café") == "café"
    odd = _pdf_text(pdf.render([{"type": "para", "text": "x ≈ 5 – 6 ‘y’"}]))
    assert "?" not in odd and "~ 5 - 6" in odd


def test_summary_table_has_no_header_row_in_either_format():
    bl = document.blocks(_real(), "issued")
    summary = _tables(bl)[0]
    assert summary["headers"] == []
    assert "Requested by" in _pdf_text(document.to_pdf(bl))
    assert "<w:tblHeader/>" not in _docx_xml(document.to_docx([summary]))
    blank = pdf.render([{"type": "table", "headers": ["", ""], "rows": [["a", "b"]]}])
    assert b" re " in blank and blank.count(b" re S") == 1  # one row box, no empty header row above it


def _docx_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode()


def test_mono_lines_wrap_inside_the_margin():
    long_line = " ".join(["word"] * 60)
    token = "x" * 300
    for raw in (long_line, token, "    " + long_line):
        lines = pdf._wrap_mono(raw, 89)
        assert all(len(line) <= 89 for line in lines) and len(lines) > 1
        assert "".join(lines).replace(" ", "") == raw.replace(" ", "")
    assert pdf._wrap_mono(long_line, 89)[0].endswith("word")  # broke at a space, not mid-word
    data = pdf.render([{"type": "mono", "text": long_line + "\n" + token}])
    assert max(len(ln) for ln in _pdf_text(data).splitlines()) <= 89
    assert token in _docx_text(docx.render([{"type": "mono", "text": token}]))


def test_backout_is_plain_paragraph_text():
    bl = document.blocks(_real(), "issued")
    i = next(n for n, b in enumerate(bl) if b.get("text") == "9 Back-out and removal")
    assert bl[i + 2] == {"type": "para", "text": LONG_BACKOUT}
    assert all(b.get("type") != "mono" for b in bl[i:i + 3])


def test_policy_one_row_per_check_and_one_risk_sentence():
    bl = document.blocks(_real(), "issued")
    policy = next(t for t in _tables(bl) if t["headers"][:1] == ["Check"] and t["headers"][1] == "Result")
    labels = [r[0] for r in policy["rows"]]
    assert len(labels) == len(set(labels)) == 3
    assert next(r for r in policy["rows"] if r[0].startswith("Nothing"))[2] == "every change in the plan is a create or an update"
    risk_cell = next(r[3] for r in _tables(bl)[0]["rows"] if r[2] == "Risk")
    assert risk_cell == "change-low"
    paras = [b["text"] for b in bl if b.get("type") == "para"]
    assert "Tier change-low." in paras and not any(p.startswith("-") for p in paras)
    high = document.blocks(_real(tier="change-high", preflight={**_real().preflight, "risk_reasons": ["costs more than 20 USD a month", "new VNet"]}), "issued")
    assert "Tier change-high, because costs more than 20 USD a month; new VNet." in [b.get("text") for b in high]


def test_after_cell_prints_key_value_lines_masked_per_value():
    assert document._cell({"location": "uaenorth", "tags": {"env": "demo", "a": 1}, "token": "(sensitive)", "k": [1, 2]}) == (
        "k: [1, 2]\nlocation: uaenorth\ntags:\n  a: 1\n  env: demo\ntoken: (sensitive)")
    assert document._cell({"a": {"b": {"c": "(sensitive)"}}}) == "a:\n  b: (sensitive)"
    bl = document.blocks(_real(), "issued")
    after = next(t for t in _tables(bl) if t["headers"][:1] == ["#"])["rows"][0][4]
    assert "tags:\n  costcentre: netops" in after and "token: (sensitive)" in after and "{" not in after
    text = _pdf_text(document.to_pdf(bl))
    assert "costcentre: netops" in text and '{"' not in text


def test_heading_is_never_left_alone_at_the_bottom_of_a_page():
    for pad in range(50, 62):
        bl = [{"type": "mono", "text": "\n".join(["filler"] * pad)},
              {"type": "heading", "level": 2, "text": "HEADING"},
              {"type": "para", "text": "first second third fourth " * 30}]
        pages = [p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf.render(bl))).pages]
        for n, text in enumerate(pages):
            if "HEADING" in text:
                assert text.split("HEADING", 1)[1].count("first") >= 1, (pad, n)
    tbl = [{"type": "mono", "text": "\n".join(["filler"] * 60)}, {"type": "heading", "level": 2, "text": "HEADING"},
           {"type": "table", "headers": ["A", "B"], "rows": [["r1", "x"], ["r2", "y"]]}]
    for p in pypdf.PdfReader(io.BytesIO(pdf.render(tbl))).pages:
        text = p.extract_text() or ""
        assert "HEADING" not in text or "r1" in text


# --- a change set: one document, ordered steps -------------------------------------------------------------------

SET_DIGEST = "a1" * 32
HUB = "/subscriptions/sub-guid/resourceGroups/rg-hub"


def _step(n, template, group, title, lines, usd, *, values=None, backout="", add=1, status="planned", **over):
    return {
        "n": n, "template": template, "resource_group": group, "title": title,
        "variables": values or {}, "plan_digest": f"{n:x}" * 64,
        "summary": {"add": add, "change": 0, "import": 0, "lines": lines,
                    "resources": [{"address": ln.split()[1], "type": ln.split()[1].split(".")[0], "name": ln.split()[-1],
                                   "action": "Create", "before": {}, "after": {"location": "uaenorth"}} for ln in lines]},
        "cost": {"monthly_usd": usd, "items": [], "unknown": []},
        "checks": [{"label": "Nothing is deleted, stopped or restarted", "ok": True}, {"label": f"step {n} in scope", "ok": True}],
        "backout": backout, "status": status, "risk": "low", "changed_ids": [], "error": "", "verified": None,
        "started_at": None, "ended_at": None, **over,
    }


def _set(**over):
    """A hub with two spokes: a group, the hub network, a spoke, its subnet, the peering."""
    steps = [
        _step(1, "resource_group", "rg-hub", "Resource group rg-hub", ["+ azurerm_resource_group.rg  rg-hub"], 0.0,
              values={"name": "rg-hub"}, backout="Remove the group last, once it is empty."),
        _step(2, "vnet", "rg-hub", "Network vnet-hub in rg-hub", ["+ azurerm_virtual_network.vnet  vnet-hub"], 0.0,
              values={"name": "vnet-hub", "address_space": ["10.0.0.0/16"]}, backout="Detach what uses the network, then remove it."),
        _step(3, "vnet", "rg-spoke1", "Network vnet-spoke1 in rg-spoke1", ["+ azurerm_virtual_network.vnet  vnet-spoke1"], 0.0,
              values={"name": "vnet-spoke1", "address_space": ["10.1.0.0/16"]}, backout="Detach what uses the network, then remove it."),
        _step(4, "subnet", "rg-spoke1", "Subnet snet-app in vnet-spoke1", ["+ azurerm_subnet.subnet  snet-app"], 0.0,
              values={"name": "snet-app", "vnet_name": "vnet-spoke1", "address_prefixes": ["10.1.1.0/24"]}, backout="Detach what uses the subnet, then remove it."),
        _step(5, "peering", "rg-hub", "Peering hub to spoke1", ["+ azurerm_virtual_network_peering.hub_to_remote  peer-hub-spoke1", "+ azurerm_virtual_network_peering.remote_to_hub  peer-spoke1-hub"], 0.0,
              values={"vnet_name": "vnet-hub", "remote_vnet": "vnet-spoke1"}, backout="Remove both peerings; the networks keep working apart.", add=2),
    ]
    base = dict(
        id="9d02b7c4abcdef0123456789", created_at=WHEN, expires_at=WHEN + 14 * 86400, requested_by="noura",
        summary="Hub and spoke network for payments", playbook_name="set", commit="3f2a91c", variables={},
        limit="sub-guid/rg-hub,rg-spoke1", tier="change-low", plan_digest=SET_DIGEST, reference="",
        diff=[], rollback_commands=[], steps=steps,
        preflight={
            "purpose": "A hub network and one spoke, peered, for the payments workload.", "plan_digest": SET_DIGEST,
            "scope": {"subscription": "sub-guid", "resource_groups": [{"name": "rg-hub", "new": True}, {"name": "rg-spoke1", "new": False}],
                      "regions": ["uaenorth"]},
            "summary": {"add": 6, "change": 0, "import": 0}, "cost": {"monthly_usd": 0.0, "items": [], "unknown": [], "priced_at": "2026-10-03"},
            "checks": [{"label": "Nothing is deleted, stopped or restarted", "ok": True}, {"label": "cost ceiling", "ok": True, "detail": "0 < 250"}],
            "advice": [], "risk_reasons": [], "usd_month_per_change": 250, "continues": "", "set": {"steps": 5, "applied": 0, "stopped_at": 0},
        },
    )
    base.update(over)
    return SimpleNamespace(**base)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_a_set_document_has_a_steps_table_a_section_per_step_and_one_approval_block():
    bl = document.blocks(_set(), "issued")
    headings = [b["text"] for b in bl if b.get("type") == "heading"]
    assert [h for h in headings if h.startswith("3.")] == [
        f"3.{n} Step {n} of 5: {t}" for n, t in enumerate(
            ["Resource group rg-hub", "Network vnet-hub in rg-hub", "Network vnet-spoke1 in rg-spoke1",
             "Subnet snet-app in vnet-spoke1", "Peering hub to spoke1"], start=1)]
    steps = next(t for t in _tables(bl) if t["headers"][:1] == ["#"] and "Template" in t["headers"])
    assert [r[0] for r in steps["rows"]] == ["1", "2", "3", "4", "5"] and steps["rows"][4][4] == "2"
    assert sum(1 for t in _tables(bl) if t["headers"][:1] == ["CR / SR number"]) == 1
    assert "ISSUED FOR APPROVAL" in str(bl) and "5 steps, in order. Adds 6, changes 0, removes 0" in str(bl)


def test_a_set_pdf_holds_the_set_digest_every_step_digest_and_every_plan_line_and_the_footer():
    rec = _set()
    data = document.build(rec, "issued", generated_at=WHEN)[document.filename(rec, "issued", "pdf", when=WHEN)]
    text = _flat(_pdf_text(data))
    assert SET_DIGEST in text.replace(" ", "")
    for step in rec.steps:
        assert step["plan_digest"] in text.replace(" ", "")
        for line in step["summary"]["lines"]:
            assert _flat(line) in text
    assert f"9d02b7c4 · set digest {SET_DIGEST[:16]}" in text and "Page 1 of" in text
    assert "(new)" in text and "Network vnet-hub in rg-hub" in text


def test_a_set_with_a_continues_names_the_change_it_carries_on_from():
    rec = _set()
    rec.preflight["continues"] = "5e77aa01bcdef"
    assert "Continues change 5e77aa01" in str(document.blocks(rec, "issued"))
    assert "Continues" not in str(document.blocks(_set(), "issued"))


def _stopped_at_3():
    rec = _set(reference="CR-20418", approved_by="noura", approved_at=WHEN + 100)
    s = rec.steps
    s[0].update(status="applied", verified=True, started_at=WHEN + 200, ended_at=WHEN + 230, changed_ids=[f"{HUB}"])
    s[1].update(status="applied", verified=True, started_at=WHEN + 231, ended_at=WHEN + 300, changed_ids=[f"{HUB}/providers/Microsoft.Network/virtualNetworks/vnet-hub"])
    s[2].update(status="failed", started_at=WHEN + 301, ended_at=WHEN + 320, error="AuthorizationFailed on rg-spoke1")
    s[3].update(status="not_run")
    s[4].update(status="not_run")
    rec.error = "Stopped at step 3 of 5: AuthorizationFailed on rg-spoke1. Steps 1 to 2 are applied and stay in Azure."
    return rec


def test_a_partial_as_applied_document_says_which_steps_were_applied_stopped_and_never_ran():
    rec = _stopped_at_3()
    text = _flat(_pdf_text(document.build(rec, "applied", generated_at=WHEN)[document.filename(rec, "applied", "pdf", when=WHEN)]))
    assert "Status: AS APPLIED, PARTIAL (2 of 5)" in text
    assert "Applied 2 of 5 steps. Stopped at step 3: AuthorizationFailed on rg-spoke1. Not run: 4, 5." in text
    assert "STOPPED: this step failed" in text and "NOT RUN: an earlier step stopped the set" in text
    assert "nothing is rolled back" in text.lower() and "CR-20418" in text
    assert text.count(f"{HUB}/providers/Microsoft.Network/virtualNetworks/vnet-hub") >= 1
    assert f"9d02b7c4 · CR-20418 · set digest {SET_DIGEST[:16]}" in text


def test_a_partial_back_out_lists_only_the_applied_steps_last_applied_first():
    bl = document.blocks(_stopped_at_3(), "applied")
    i = next(n for n, b in enumerate(bl) if b.get("text") == "9 Back-out and removal")
    j = next(n for n, b in enumerate(bl) if b.get("text") == "10 Approval")
    paras = [b["text"] for b in bl[i:j] if b.get("type") == "para"]
    named = [p for p in paras if p.startswith("Step ")]
    assert named[0].startswith("Step 3 failed part-way")
    assert [p.split(":")[0] for p in named[1:]] == ["Step 2", "Step 1"]
    assert not any("Step 4" in p or "Step 5" in p for p in paras)


def test_a_complete_and_an_untouched_set_say_so_in_the_status_line():
    done = _set(reference="CR-1")
    for s in done.steps:
        s.update(status="applied", verified=True)
    assert "Status: AS APPLIED" in str(document.blocks(done, "applied")) and "PARTIAL" not in str(document.blocks(done, "applied"))
    none = _set(reference="CR-1")
    assert "NOT APPLIED (0 of 5)" in str(document.blocks(none, "applied"))
    back = [b["text"] for b in document.blocks(none, "applied") if b.get("type") == "para"]
    assert "No step was applied, so there is nothing to back out." in back


def test_the_issued_set_back_out_is_every_step_last_first():
    bl = document.blocks(_set(), "issued")
    i = next(n for n, b in enumerate(bl) if b.get("text") == "9 Back-out and removal")
    named = [b["text"].split(":")[0] for b in bl[i:] if b.get("type") == "para" and b["text"].startswith("Step ")]
    assert named == ["Step 5", "Step 4", "Step 3", "Step 2", "Step 1"]


def test_a_set_docx_carries_the_same_words():
    rec = _set()
    text = _docx_text(document.build(rec, "issued", generated_at=WHEN)[document.filename(rec, "issued", "docx", when=WHEN)])
    assert SET_DIGEST in text and all(s["plan_digest"] in text for s in rec.steps)


def test_a_single_changes_document_is_byte_for_byte_what_it_was():
    """Hashes taken before the set code was added: a set must not move a single change by a byte."""
    import hashlib
    import json

    rec = _record(reference="CR-20418", approved_by="noura", approved_at=WHEN + 100)
    applied = {"started_at": WHEN + 200, "ended_at": WHEN + 300, "verified": True, "changed_ids": ["/subscriptions/x/resourceGroups/rg"]}
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    assert sha(json.dumps(document.blocks(rec, "issued"), sort_keys=True).encode()) == "52d1763f1a5732515fabd491bcc0d3bf7a6ccd608bef8fe68fe5a547378c55db"
    assert sha(json.dumps(document.blocks(rec, "applied", applied), sort_keys=True).encode()) == "39fd138bd2a893dc4b0a51ca7c5ddea4ce839c52a35d101b44335b1c6e313776"
    issued = document.build(rec, "issued", generated_at=WHEN)[document.filename(rec, "issued", "pdf", when=WHEN)]
    done = document.build(rec, "applied", applied, generated_at=WHEN)[document.filename(rec, "applied", "pdf", when=WHEN)]
    assert sha(issued) == "3f736b8c3364d3cefabae8634e68597375fdcb64e2b9b8bf24be709ee3523125"
    assert sha(done) == "e063caa4f2257a410916a117b10ac6533f45ab6da6610d2f4032e1c80572648d"
