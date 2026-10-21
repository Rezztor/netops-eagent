"""What of a job's output reaches the model: every command, without Ansible's banners."""

import json

from plugins._ea_ansible.helpers.excerpt import excerpt

BANNER = "TASK [Show the output from a {} device] " + "*" * 40 + "\nskipping: [c3725-lab]\n\n"


def _run(outputs: dict[str, list[str]]) -> str:
    block = json.dumps({"msg": outputs}, indent=4)
    return (
        "PLAY [Run the show commands the engineer approved] " + "*" * 30 + "\n\n"
        "TASK [Run the commands on a Cisco IOS device] " + "*" * 30 + "\nok: [c3725-lab]\n\n"
        "TASK [Show the output from a Cisco IOS device] " + "*" * 30 + f"\nok: [c3725-lab] => {block}\n\n"
        + "".join(BANNER.format(p) for p in ("NX-OS", "EOS", "Junos", "SONiC", "sonic_community"))
        + "PLAY RECAP " + "*" * 60 + "\nc3725-lab : ok=4 changed=0 unreachable=0 failed=0 skipped=11\n"
    )


def test_a_three_command_read_keeps_all_three_commands():
    # The read that lost its first command on c3725-lab: Ansible prints the commands in alphabetical order, and
    # the last 6,000 characters of the raw output began halfway through the second.
    outputs = {
        "show interfaces FastEthernet1/15": ["FastEthernet1/15 is up, line protocol is down"] + ["  counters"] * 40,
        "show ip interface brief": [f"FastEthernet1/{n} unassigned YES unset up down" for n in range(120)],
        "show version": ["Cisco IOS Software, 3700 Software"] + ["  text " * 12] * 150 + ["Configuration register is 0x2102"],
    }
    shown = excerpt(_run(outputs))
    for command in outputs:
        assert f"# c3725-lab: {command}" in shown
    assert "FastEthernet1/15 is up, line protocol is down" in shown
    assert "Configuration register is 0x2102" in shown  # a long output keeps its tail as well as its head
    assert "lines not shown" in shown and "narrower command" in shown
    assert "skipping:" not in shown and "TASK [" not in shown
    assert "ok=4" in shown
    assert len(shown) < 14000


def test_a_short_read_is_shown_whole():
    shown = excerpt(_run({"show clock": ["12:00:00.000 UTC Sun Oct 4 2026"]}))
    assert "12:00:00.000 UTC" in shown and "lines not shown" not in shown


def test_output_it_cannot_read_as_commands_loses_only_the_banners():
    raw = "TASK [x] ***\nskipping: [a]\n\nfatal: [a]: FAILED! => {\"msg\": \"timed out\"}\n\nPLAY RECAP ***\na : ok=0 failed=1\n"
    shown = excerpt(raw)
    assert "FAILED!" in shown and "failed=1" in shown and "skipping" not in shown
