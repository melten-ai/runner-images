import json
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_bootstrap_binds_immutable_repository_and_workflow_without_keys(
    tmp_path, monkeypatch
):
    configuration = {
        "repository": "example-org/runner-images",
        "gcp_configuration": "example",
        "gcp_project": "example-project",
        "gcp_zone": "us-central1-a",
        "gcp_network": "example-network",
        "azure_subscription": "00000000-0000-0000-0000-000000000000",
        "azure_tenant": "11111111-1111-1111-1111-111111111111",
        "azure_build_group": "example-builds",
        "azure_image_group": "example-images",
        "azure_location": "centralus",
        "azure_vm_size": "Standard_D4s_v5",
        "azure_gallery": "examplegallery",
        "azure_definition": "example-image",
    }
    path = tmp_path / "private-configuration.json"
    path.write_text(json.dumps(configuration))
    state = {
        "calls": [],
        "documents": [],
        "created": set(),
        "assignments": set(),
        "delays": [],
    }

    def run(command, **kwargs):
        state["calls"].append(command)
        response = {}
        if command[:3] == ["gh", "api", "repos/example-org/runner-images"]:
            response = {"id": 42, "owner": {"id": 7}}
        if command[0] == "gcloud":
            if command[2:4] == ["projects", "describe"]:
                response = {"projectNumber": "12345"}
            elif "list" in command:
                key = (
                    "service-accounts"
                    if "service-accounts" in command
                    else "roles"
                    if "roles" in command
                    else "providers"
                    if "providers" in command
                    else "pools"
                    if "workload-identity-pools" in command
                    else "networks"
                )
                response = [{"name": key}] if key in state["created"] else []
            elif "create" in command or "create-oidc" in command:
                key = (
                    "service-accounts"
                    if "service-accounts" in command
                    else "roles"
                    if "roles" in command
                    else "providers"
                    if "providers" in command
                    else "pools"
                    if "workload-identity-pools" in command
                    else "networks"
                )
                state["created"].add(key)
            for argument in command:
                if argument.startswith("--file="):
                    state["documents"].append(
                        json.loads(Path(argument[7:]).read_text())
                    )
            if (
                "add-iam-policy-binding" in command
                and "projects" in command
                and not state.get("propagated")
            ):
                state["propagated"] = True
                return subprocess.CompletedProcess(
                    command, 1, "", "Service account example does not exist"
                )
        if command[0] == "az":
            if command[1:3] == ["account", "show"]:
                response = {"tenantId": configuration["azure_tenant"]}
            elif command[1:3] == ["group", "list"]:
                response = (
                    [{"name": "example-builds"}] if "group" in state["created"] else []
                )
            elif command[1:3] == ["group", "create"]:
                state["created"].add("group")
            elif command[1:4] == ["sig", "image-definition", "list"]:
                response = (
                    [{"name": "example-image"}]
                    if "definition" in state["created"]
                    else []
                )
            elif command[1:4] == ["sig", "image-definition", "create"]:
                state["created"].add("definition")
            elif command[1:4] == ["ad", "app", "list"]:
                response = (
                    [{"id": "example-app", "appId": "example-client"}]
                    if "app" in state["created"]
                    else []
                )
            elif command[1:4] == ["ad", "app", "create"]:
                state["created"].add("app")
                response = {"id": "example-app", "appId": "example-client"}
            elif command[1:4] == ["ad", "sp", "list"]:
                response = (
                    [{"id": "example-principal"}] if "sp" in state["created"] else []
                )
            elif command[1:4] == ["ad", "sp", "create"]:
                state["created"].add("sp")
                response = {"id": "example-principal"}
            elif command[1] == "rest":
                if command[command.index("--method") + 1] == "GET":
                    response = {
                        "value": [{"name": "github-cloud-main", "id": "example-fic"}]
                        if "fic" in state["created"]
                        else []
                    }
                else:
                    state["created"].add("fic")
                    state["documents"].append(
                        json.loads(Path(command[-1][1:]).read_text())
                    )
            elif command[1:4] == ["role", "definition", "list"]:
                response = [{}] if "catalog" in state["created"] else []
            elif command[1:4] == ["role", "definition", "create"]:
                state["created"].add("catalog")
                state["documents"].append(
                    json.loads(
                        Path(
                            command[command.index("--role-definition") + 1][1:]
                        ).read_text()
                    )
                )
            elif command[1:4] == ["role", "assignment", "list"]:
                role = re.search(
                    "roleDefinitionName=='([^']+)'",
                    command[command.index("--query") + 1],
                ).group(1)
                scope = command[command.index("--scope") + 1]
                response = [{}] if (role, scope) in state["assignments"] else []
            elif command[1:4] == ["role", "assignment", "create"]:
                state["assignments"].add(
                    (
                        command[command.index("--role") + 1],
                        command[command.index("--scope") + 1],
                    )
                )
        return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda value: state["delays"].append(value))
    monkeypatch.setattr(sys, "argv", ["configure-auth.py", str(path)])
    script = Path("cloud/configure-auth.py")
    runpy.run_path(str(script), run_name="__main__")
    runpy.run_path(str(script), run_name="__main__")
    # Existing identities/groups are reused; a transient IAM propagation failure
    # retries the same narrow binding instead of granting a broader role.
    assert state["delays"] == [2]
    assert sum(c[:4] == ["az", "ad", "app", "create"] for c in state["calls"]) == 1
    assert sum(c[:3] == ["az", "group", "create"] for c in state["calls"]) == 1
    gcp_role = next(d for d in state["documents"] if "includedPermissions" in d)
    assert "compute.instances.setServiceAccount" in gcp_role["includedPermissions"]
    assert not any(
        p.startswith("iam.") or "setIamPolicy" in p
        for p in gcp_role["includedPermissions"]
    )
    fic = next(d for d in state["documents"] if "claimsMatchingExpression" in d)
    expression = fic["claimsMatchingExpression"]["value"]
    assert "claims['repository_id'] eq '42'" in expression
    assert "claims['repository_owner_id'] eq '7'" in expression
    assert "cloud-image.yml@refs/heads/main" in expression
    network_binding = next(
        c
        for c in state["calls"]
        if any("runnerImagesNetworkPolicy" in a for a in c)
        and "add-iam-policy-binding" in c
    )
    assert any(
        "resource.name == 'projects/example-project/global/networks/example-network'"
        in a
        for a in network_binding
    )
    assert len(state["assignments"]) == 4
    assert not any(
        "secret" in " ".join(c).lower() or "keys" in c for c in state["calls"]
    )
    assert not any(
        c[:4] == ["az", "ad", "sp", "create-for-rbac"] for c in state["calls"]
    )


def test_auth_setup_failure_does_not_continue_or_leak_into_other_provider(
    tmp_path, monkeypatch
):
    path = tmp_path / "configuration.json"
    path.write_text(
        json.dumps(
            {
                "repository": "example-org/runner-images",
                "gcp_project": "example-project",
                "gcp_configuration": "example",
            }
        )
    )
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        if command[0] == "gh":
            return subprocess.CompletedProcess(
                command, 0, '{"id":42,"owner":{"id":7}}', ""
            )
        return subprocess.CompletedProcess(command, 1, "", "PERMISSION_DENIED")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(
        sys, "argv", ["configure-auth.py", str(path), "--provider", "gcp"]
    )
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        runpy.run_path("cloud/configure-auth.py", run_name="__main__")
    assert not any(c[0] == "az" for c in calls)
