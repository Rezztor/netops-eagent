# Firewall fixtures

Written from the vendors' documented output, **not captured from a device**. Field names come from
the pages below; every value (serials, addresses, names, counts) is invented. Where a page gives
a field list or a short example, the fixture keeps that shape; the rest is the documented
structure filled in. Correct these on the first live run.

FortiOS (REST API, GET with `Authorization: Bearer <token>`):

- API token, REST API administrator, trusted hosts, `Authorization: Bearer`:
  <https://docs.fortinet.com/document/fortigate/7.4.0/administration-guide/940602/rest-api>
- `monitor/system/status` (`results.model_name`, `model_number`, `model`, `hostname`; top-level `serial`,
  `version`, `build`), `monitor/system/ha-peer`, `monitor/router/ipv4`, `monitor/vpn/ipsec`,
  `monitor/system/interface`, `monitor/system/resource/usage`, `cmdb/firewall/policy` (with the
  documented `format=` field filter) and `cmdb/system/ha`: the FortiOS REST API reference
  (<https://fndn.fortinet.net>, needs a Fortinet account) and the Ansible `fortios_monitor_fact` module
  documentation, which lists the same selectors:
  <https://docs.ansible.com/ansible/latest/collections/fortinet/fortios/fortios_monitor_fact_module.html>

PAN-OS (XML API, GET with `X-PAN-KEY: <key>`):

- Authentication and the `X-PAN-KEY` header:
  <https://docs.paloaltonetworks.com/pan-os/11-0/pan-os-panorama-api/about-the-pan-os-xml-api/structure-of-a-pan-os-xml-api-request/api-authentication-and-security>
- `type=op` and `type=config&action=show`: the PAN-OS XML API request types
  (<https://docs.paloaltonetworks.com/pan-os/10-2/pan-os-panorama-api/pan-os-xml-api-request-types>).
- `show system info` response (`hostname`, `model`, `serial`, `family`, `uptime`; `sw-version`) as quoted in
  <https://live.paloaltonetworks.com/t5/general-topics/palo-alto-api-for-end-of-life-services/td-p/557468>;
  `show high-availability state` (`enabled`, `group/mode`, `group/local-info/state`) as used in
  <https://live.paloaltonetworks.com/t5/automation-api-discussions/fw-monitoring-through-xml-api/td-p/6129>.
- `show interface all` (`ifnet` and `hw` entries), `show routing route`, `show vpn flow`,
  `show system resources` (`top` output) and `show session info`: the PAN-OS CLI Operational Command
  Hierarchy, whose XML mirrors the command words. The XML element names for these are the least certain
  part (the `show vpn flow` layout in particular); the shaper looks for any entry with a name and a state.
