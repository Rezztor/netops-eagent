"""The gate that refuses a model-written command which would reach Azure outside the azure_* tools
(`extensions/python/tool_execute_before/_90_azure_no_cloud_cli.py`). No database, no network.

The incident: an agent, unable to give the change tool a subscription GUID, installed azure-cli in the runner and started
`az login --use-device-code`, sending the engineer the device code. Had they entered it, the agent would have held the
engineer's own Azure rights with a CLI that deletes and stops anything, past every control."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from helpers.errors import RepairableException
from plugins._ea_azure.extensions.python.tool_execute_before._90_azure_no_cloud_cli import AzureNoCloudCli, _matched


REFUSED = [
    "az login --use-device-code",
    "az account show",
    "pip install azure-cli",
    "python -m pip install azure-identity msal",
    "apt-get install -y azure-cli",
    "curl -s https://login.microsoftonline.com/common/oauth2/v2.0/devicecode -d x",
    "Connect-AzAccount -UseDeviceAuthentication",
    "Install-Module Az -Scope CurrentUser; Connect-AzAccount",
    "azd auth login",
    "cd azure-lab && terraform apply -auto-approve",
    "tofu init && tofu apply  # provider azurerm",
    "az bicep build -f main.bicep",
    "curl https://management.azure.com/subscriptions?api-version=2022-12-01",
]

ALLOWED = [
    "echo done",
    "python3 jazz.py --lazy",          # 'az' sits inside other words
    "x = 1  # a lazy comment about azimuth",
    "git clone https://example.com/azores.git",
    "pip install requests pandas",
    "ls -la && cat /etc/hostname",
    "import boto3  # a different cloud",
    "terraformer_output = 1",          # 'terraform' inside a longer word
    "az = 90  # azimuth in degrees",     # a variable named az is not the CLI
    "terraform plan -out aws.plan",     # Terraform for something that is not Azure
]


@pytest.mark.parametrize("code", REFUSED)
def test_a_cloud_cli_or_sign_in_is_refused(code):
    assert _matched(code), code


@pytest.mark.parametrize("code", ALLOWED)
def test_an_ordinary_command_is_allowed(code):
    assert not _matched(code), code


def _fire(tool="code_execution_tool", **args):
    ext = AzureNoCloudCli(agent=SimpleNamespace(number=0))
    return asyncio.run(ext.execute(tool_name=tool, tool_args=args))


def test_the_extension_refuses_a_device_code_sign_in_and_lets_a_plain_script_through():
    with pytest.raises(RepairableException, match="azure_\\* tools"):
        _fire(runtime="terminal", code="az login --use-device-code")
    with pytest.raises(RepairableException):
        _fire(tool="input", keyboard="az login\n")  # the terminal-input tool is gated too, every string scanned
    assert _fire(runtime="python", code="print('hello')") is None


def test_only_the_runner_tools_are_gated():
    assert _fire(tool="azure_inventory", query="az") is None  # 'az' in another tool's argument is not this gate's business
