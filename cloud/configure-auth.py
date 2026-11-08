"""Provision dedicated OIDC identities from a private configuration JSON file.

Run as a cloud administrator; build jobs receive only the narrower generated roles.
Configuration is never copied into the public recipe or release evidence.
"""

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("configuration", type=Path)
parser.add_argument("--provider", choices=["gcp", "azure", "both"], default="both")
args = parser.parse_args()
settings = json.loads(args.configuration.read_text())
repository = settings["repository"]


def run(command):
    for attempt in range(6):
        result = subprocess.run(command, text=True, capture_output=True, timeout=300)
        if not result.returncode:
            return json.loads(result.stdout) if result.stdout.strip() else None
        propagating = (
            "add-iam-policy-binding" in command
            and "Service account" in result.stderr
            and "does not exist" in result.stderr
        ) or ("PrincipalNotFound" in result.stderr and "assignment" in command)
        if not propagating or attempt == 5:
            raise RuntimeError(result.stderr[-3000:])
        time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable retry state")


repo = run(["gh", "api", "repos/" + repository])
repo_id = str(repo["id"])
owner_id = str(repo["owner"]["id"])
job_ref = repository + "/.github/workflows/cloud-image.yml@refs/heads/main"

with tempfile.TemporaryDirectory() as directory:
    temporary = Path(directory)

    def document(name, value):
        path = temporary / name
        path.write_text(json.dumps(value))
        return str(path)

    def gcp(*command):
        return run(
            [
                "gcloud",
                "--configuration=" + settings["gcp_configuration"],
                *command,
                "--project=" + settings["gcp_project"],
                "--format=json",
                "--quiet",
            ]
        )

    if args.provider in ("gcp", "both"):
        project = settings["gcp_project"]
        number = str(gcp("projects", "describe", project)["projectNumber"])
        gcp(
            "services",
            "enable",
            "iamcredentials.googleapis.com",
            "iam.googleapis.com",
            "sts.googleapis.com",
            "compute.googleapis.com",
        )
        if not gcp(
            "compute", "networks", "list", "--filter=name=" + settings["gcp_network"]
        ):
            gcp(
                "compute",
                "networks",
                "create",
                settings["gcp_network"],
                "--subnet-mode=auto",
            )
        service_account = (
            "runner-images-builder@" + project + ".iam.gserviceaccount.com"
        )
        if not gcp(
            "iam", "service-accounts", "list", "--filter=email=" + service_account
        ):
            gcp(
                "iam",
                "service-accounts",
                "create",
                "runner-images-builder",
                "--display-name=Runner image build automation",
            )
        permissions = [
            "compute.projects.get",
            "compute.regions.get",
            "compute.regions.list",
            "compute.zones.get",
            "compute.zones.list",
            "compute.machineTypes.get",
            "compute.machineTypes.list",
            "compute.diskTypes.get",
            "compute.instances.create",
            "compute.instances.delete",
            "compute.instances.get",
            "compute.instances.list",
            "compute.instances.setLabels",
            "compute.instances.setMetadata",
            "compute.instances.setTags",
            "compute.instances.setScheduling",
            # The Compute insert API requires this even for an empty SA list.
            # No iam.serviceAccounts.actAs grant: guests remain identity-free.
            "compute.instances.setServiceAccount",
            "compute.instances.start",
            "compute.instances.stop",
            "compute.instances.getSerialPortOutput",
            "compute.disks.create",
            "compute.disks.delete",
            "compute.disks.get",
            "compute.disks.list",
            "compute.disks.setLabels",
            "compute.disks.use",
            "compute.disks.useReadOnly",
            "compute.images.create",
            "compute.images.delete",
            "compute.images.get",
            "compute.images.list",
            "compute.images.setLabels",
            "compute.images.useReadOnly",
            "compute.firewalls.create",
            "compute.firewalls.delete",
            "compute.firewalls.get",
            "compute.firewalls.list",
            "compute.networks.get",
            "compute.networks.list",
            "compute.networks.use",
            "compute.subnetworks.get",
            "compute.subnetworks.list",
            "compute.subnetworks.use",
            "compute.subnetworks.useExternalIp",
            "compute.globalOperations.get",
            "compute.regionOperations.get",
            "compute.zoneOperations.get",
            "serviceusage.services.use",
            "resourcemanager.projects.get",
        ]
        role = document(
            "gcp-role.json",
            {
                "title": "Runner Images Builder",
                "stage": "GA",
                "description": "VM/image lifecycle without identity administration",
                "includedPermissions": permissions,
            },
        )
        exists = gcp("iam", "roles", "list", "--filter=name:runnerImagesBuilder")
        gcp(
            "iam",
            "roles",
            "update" if exists else "create",
            "runnerImagesBuilder",
            "--file=" + role,
        )
        gcp(
            "projects",
            "add-iam-policy-binding",
            project,
            "--member=serviceAccount:" + service_account,
            "--role=projects/" + project + "/roles/runnerImagesBuilder",
            "--condition=None",
        )
        network_role = document(
            "gcp-network-role.json",
            {
                "title": "Runner Images Network Policy",
                "stage": "GA",
                "description": "Firewall policy for the dedicated build network only",
                "includedPermissions": ["compute.networks.updatePolicy"],
            },
        )
        network_role_exists = gcp(
            "iam", "roles", "list", "--filter=name:runnerImagesNetworkPolicy"
        )
        gcp(
            "iam",
            "roles",
            "update" if network_role_exists else "create",
            "runnerImagesNetworkPolicy",
            "--file=" + network_role,
        )
        network_name = (
            "projects/" + project + "/global/networks/" + settings["gcp_network"]
        )
        gcp(
            "projects",
            "add-iam-policy-binding",
            project,
            "--member=serviceAccount:" + service_account,
            "--role=projects/" + project + "/roles/runnerImagesNetworkPolicy",
            "--condition=title=runner-images-network,expression=resource.name == '"
            + network_name
            + "'",
        )
        pool = "runner-images"
        pools = gcp(
            "iam",
            "workload-identity-pools",
            "list",
            "--location=global",
            "--filter=name:" + pool,
        )
        if not pools:
            gcp(
                "iam",
                "workload-identity-pools",
                "create",
                pool,
                "--location=global",
                "--display-name=Runner image GitHub builds",
            )
        provider_commands = ("iam", "workload-identity-pools", "providers")
        providers = gcp(
            *provider_commands,
            "list",
            "--location=global",
            "--workload-identity-pool=" + pool,
        )
        condition = (
            f"assertion.repository_id == '{repo_id}' "
            f"&& assertion.repository_owner_id == '{owner_id}' "
            "&& assertion.ref == 'refs/heads/main' "
            f"&& assertion.job_workflow_ref == '{job_ref}'"
        )
        gcp(
            *provider_commands,
            "update-oidc" if providers else "create-oidc",
            "github",
            "--location=global",
            "--workload-identity-pool=" + pool,
            "--issuer-uri=https://token.actions.githubusercontent.com",
            "--attribute-mapping=google.subject=assertion.sub,attribute.repository_id=assertion.repository_id",
            "--attribute-condition=" + condition,
        )
        gcp(
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            service_account,
            "--member=principalSet://iam.googleapis.com/projects/"
            + number
            + "/locations/global/workloadIdentityPools/"
            + pool
            + "/attribute.repository_id/"
            + repo_id,
            "--role=roles/iam.workloadIdentityUser",
        )
        variables = {
            "GCP_PROJECT": project,
            "GCP_MACHINE_TYPE": settings.get("gcp_machine_type", "e2-standard-4"),
            "GCP_ZONE": settings["gcp_zone"],
            "GCP_NETWORK": settings["gcp_network"],
            "GCP_SERVICE_ACCOUNT": service_account,
            "GCP_WORKLOAD_IDENTITY_PROVIDER": "projects/"
            + number
            + "/locations/global/workloadIdentityPools/"
            + pool
            + "/providers/github",
        }
        for key, value in variables.items():
            run(["gh", "variable", "set", key, "--repo", repository, "--body", value])
        print(
            "GCP OIDC identity, network and operation role configured.",
            flush=True,
        )

    if args.provider in ("azure", "both"):
        subscription = settings["azure_subscription"]
        tenant = settings["azure_tenant"]
        account = run(
            ["az", "account", "show", "--subscription", subscription, "-o", "json"]
        )
        if account["tenantId"] != tenant:
            raise ValueError("Azure tenant mismatch")
        run(["az", "account", "set", "--subscription", subscription])
        build_group = settings["azure_build_group"]
        image_group = settings["azure_image_group"]
        gallery = settings["azure_gallery"]
        definition = settings["azure_definition"]
        group_id = "/subscriptions/" + subscription + "/resourceGroups/"
        gallery_id = (
            group_id + image_group + "/providers/Microsoft.Compute/galleries/" + gallery
        )
        groups = run(
            ["az", "group", "list", "--subscription", subscription, "-o", "json"]
        )
        if not any(g["name"].lower() == build_group.lower() for g in groups):
            run(
                [
                    "az",
                    "group",
                    "create",
                    "--name",
                    build_group,
                    "--location",
                    settings["azure_location"],
                    "--subscription",
                    subscription,
                    "--tags",
                    "owner=runner-images",
                    "-o",
                    "json",
                ]
            )
        definitions = run(
            [
                "az",
                "sig",
                "image-definition",
                "list",
                "--gallery-name",
                gallery,
                "--resource-group",
                image_group,
                "--subscription",
                subscription,
                "-o",
                "json",
            ]
        )
        if not any(d["name"] == definition for d in definitions):
            run(
                [
                    "az",
                    "sig",
                    "image-definition",
                    "create",
                    "--gallery-name",
                    gallery,
                    "--resource-group",
                    image_group,
                    "--gallery-image-definition",
                    definition,
                    "--publisher",
                    "RunnerImages",
                    "--offer",
                    "Ubuntu",
                    "--sku",
                    "ubuntu-2404",
                    "--os-type",
                    "Linux",
                    "--os-state",
                    "Generalized",
                    "--hyper-v-generation",
                    "V2",
                    "--architecture",
                    "x64",
                    "--subscription",
                    subscription,
                    "-o",
                    "json",
                ]
            )
        apps = run(
            [
                "az",
                "ad",
                "app",
                "list",
                "--filter",
                "displayName eq 'runner-images-github'",
                "-o",
                "json",
            ]
        )
        if len(apps) > 1:
            raise ValueError("ambiguous Azure app identity")
        app = (
            apps[0]
            if apps
            else run(
                [
                    "az",
                    "ad",
                    "app",
                    "create",
                    "--display-name",
                    "runner-images-github",
                    "--sign-in-audience",
                    "AzureADMyOrg",
                    "-o",
                    "json",
                ]
            )
        )
        principals = run(
            [
                "az",
                "ad",
                "sp",
                "list",
                "--filter",
                "appId eq '" + app["appId"] + "'",
                "-o",
                "json",
            ]
        )
        principal = (
            principals[0]
            if principals
            else run(["az", "ad", "sp", "create", "--id", app["appId"], "-o", "json"])
        )
        fic_url = (
            "https://graph.microsoft.com/beta/applications/"
            + app["id"]
            + "/federatedIdentityCredentials"
        )
        credentials = run(["az", "rest", "--method", "GET", "--url", fic_url])["value"]
        expression = (
            "claims['sub'] matches 'repo:*:ref:refs/heads/main' "
            f"and claims['repository_id'] eq '{repo_id}' "
            f"and claims['repository_owner_id'] eq '{owner_id}' "
            f"and claims['job_workflow_ref'] eq '{job_ref}'"
        )
        credential = document(
            "azure-federation.json",
            {
                "name": "github-cloud-main",
                "issuer": "https://token.actions.githubusercontent.com",
                "audiences": ["api://AzureADTokenExchange"],
                "claimsMatchingExpression": {"languageVersion": 1, "value": expression},
            },
        )
        existing = next(
            (c for c in credentials if c["name"] == "github-cloud-main"), None
        )
        run(
            [
                "az",
                "rest",
                "--method",
                "PATCH" if existing else "POST",
                "--url",
                fic_url + ("/" + existing["id"] if existing else ""),
                "--body",
                "@" + credential,
            ]
        )
        catalog = document(
            "azure-catalog-role.json",
            {
                "Name": "Runner Images Catalog Reader",
                "IsCustom": True,
                "Description": "Public catalog reads for image builds",
                "Actions": [
                    "Microsoft.Resources/subscriptions/read",
                    "Microsoft.Resources/subscriptions/locations/read",
                    "Microsoft.Compute/locations/*/read",
                    "Microsoft.Compute/skus/read",
                    "Microsoft.Network/locations/*/read",
                ],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/" + subscription],
            },
        )
        roles = run(
            [
                "az",
                "role",
                "definition",
                "list",
                "--name",
                "Runner Images Catalog Reader",
                "--subscription",
                subscription,
                "-o",
                "json",
            ]
        )
        if not roles:
            run(
                [
                    "az",
                    "role",
                    "definition",
                    "create",
                    "--role-definition",
                    "@" + catalog,
                    "--subscription",
                    subscription,
                    "-o",
                    "json",
                ]
            )
        scopes = [
            ("Contributor", group_id + build_group),
            ("Reader", group_id + image_group),
            ("Contributor", gallery_id + "/images/" + definition),
            ("Runner Images Catalog Reader", "/subscriptions/" + subscription),
        ]
        for role, scope in scopes:
            assignments = run(
                [
                    "az",
                    "role",
                    "assignment",
                    "list",
                    "--scope",
                    scope,
                    "--subscription",
                    subscription,
                    "--query",
                    "[?principalId=='"
                    + principal["id"]
                    + "' && roleDefinitionName=='"
                    + role
                    + "']",
                    "-o",
                    "json",
                ]
            )
            if not assignments:
                run(
                    [
                        "az",
                        "role",
                        "assignment",
                        "create",
                        "--assignee-object-id",
                        principal["id"],
                        "--assignee-principal-type",
                        "ServicePrincipal",
                        "--role",
                        role,
                        "--scope",
                        scope,
                        "--subscription",
                        subscription,
                        "-o",
                        "json",
                    ]
                )
        variables = {
            "AZURE_VM_SIZE": settings["azure_vm_size"],
            "AZURE_CLIENT_ID": app["appId"],
            "AZURE_TENANT_ID": tenant,
            "AZURE_SUBSCRIPTION_ID": subscription,
            "AZURE_LOCATION": settings["azure_location"],
            "AZURE_BUILD_RESOURCE_GROUP": build_group,
            "AZURE_RESOURCE_GROUP": image_group,
            "AZURE_GALLERY": gallery,
            "AZURE_IMAGE_DEFINITION": definition,
        }
        for key, value in variables.items():
            run(["gh", "variable", "set", key, "--repo", repository, "--body", value])
        print(
            "Azure OIDC identity and isolated build/image roles configured.",
            flush=True,
        )
