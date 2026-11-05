#!/usr/bin/env python3
"""Knowledge Hub - Intune compliance policy as code (CS2, LO3 endpoint automation).

Manages the Windows compliance policy cp-kh-windows-baseline through the
Microsoft Graph API, so the policy and its assignment live in Git instead of
only in the Intune portal.

Modes:
    export  Read the live policy from Intune and save it as JSON in policies/.
    apply   Read the JSON from Git and make Intune match it:
            create the policy if it does not exist, otherwise update it, then
            set the assignment to ONLY the group in KH_ASSIGN_GROUP_ID
            (no "All devices", no exclusions).
    show    Print the policy's current assignments.

Why automate the assignment: on 5 Nov the group was added under "Excluded"
instead of "Included" in the portal. An exclusion wins, so the device got no
compliance policy (Error 65001). The script always writes the same, reviewed
assignment, so that mistake cannot happen again.

Settings come from environment variables or a .env file (never hard-coded):
    KH_TENANT_ID         Directory (tenant) ID of IronWolfBV.onmicrosoft.com
    KH_CLIENT_ID         Application (client) ID of the app registration
    KH_CLIENT_SECRET     Client secret value (password manager, never in Git)
    KH_ASSIGN_GROUP_ID   Object ID of grp-kh-staff-devices
    KH_POLICY_FILE       optional, default policies/cp-kh-windows-baseline.json

Required Graph permission (application, admin consent):
    DeviceManagementConfiguration.ReadWrite.All   (nothing else: least privilege)

Exit codes: 0 OK, 1 temporary failure (retry later),
2 configuration / authentication / permission error, 3 rejected by Graph.
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# beta, not v1.0: in v1.0 the Windows compliance policy does not expose the
# Defender / firewall settings (activeFirewallRequired, defenderEnabled,
# rtpEnabled, antivirusRequired, antiSpywareRequired). An export from v1.0
# silently dropped 5 of the 6 baseline settings (found 5 Nov). The Intune
# portal uses beta too. Trade-off: beta can change without notice, so check
# the exported JSON after every export.
GRAPH_URL = "https://graph.microsoft.com/beta"
POLICIES_URL = f"{GRAPH_URL}/deviceManagement/deviceCompliancePolicies"
LOGIN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY_FILE = SCRIPT_DIR / "policies" / "cp-kh-windows-baseline.json"

REQUEST_TIMEOUT = (5, 60)  # connect, read
MAX_RETRIES = 3            # for 429 / 5xx

# Fields Graph sets itself; they must not be sent back on create/update
READ_ONLY_FIELDS = {"id", "createdDateTime", "lastModifiedDateTime", "version"}

# Graph rejects a new compliance policy without at least one action.
# This is the portal default: mark noncompliant immediately (grace period 0).
DEFAULT_ACTIONS = [
    {
        "ruleName": "PasswordRequired",
        "scheduledActionConfigurations": [
            {
                "actionType": "block",
                "gracePeriodHours": 0,
                "notificationTemplateId": "",
                "notificationMessageCCList": [],
            }
        ],
    }
]

EXIT_OK = 0
EXIT_TEMPORARY = 1
EXIT_CONFIG = 2
EXIT_REJECTED = 3

log = logging.getLogger("kh-intune")


class GraphError(Exception):
    """A Graph or sign-in call failed; carries the exit code to use."""

    def __init__(self, message, exit_code):
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------- settings

def load_settings(need_group=False):
    """Read the settings from the environment / .env and check them."""

    load_dotenv(SCRIPT_DIR / ".env")

    names = ["KH_TENANT_ID", "KH_CLIENT_ID", "KH_CLIENT_SECRET"]
    if need_group:
        names.append("KH_ASSIGN_GROUP_ID")

    settings = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in settings.items()
               if not value or value.startswith("<")]
    if missing:
        raise GraphError(
            "Missing settings: " + ", ".join(missing)
            + " (copy .env.example to .env and fill it in)", EXIT_CONFIG)

    settings["KH_POLICY_FILE"] = Path(
        os.getenv("KH_POLICY_FILE", "") or DEFAULT_POLICY_FILE)
    return settings


# ---------------------------------------------------------------- Graph calls

def get_token(settings):
    """Sign in as the app (client credentials flow) and return a token."""

    url = LOGIN_URL.format(tenant=settings["KH_TENANT_ID"])
    data = {
        "grant_type": "client_credentials",
        "client_id": settings["KH_CLIENT_ID"],
        "client_secret": settings["KH_CLIENT_SECRET"],
        "scope": SCOPE,
    }
    try:
        response = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as error:
        raise GraphError(f"Cannot reach the sign-in service: {error}",
                         EXIT_TEMPORARY) from error

    if response.status_code != 200:
        body = _json_or_empty(response)
        # e.g. AADSTS7000215 = wrong secret, AADSTS700016 = wrong client ID,
        # AADSTS90002 = wrong tenant ID
        detail = body.get("error_description", response.text[:300])
        raise GraphError("Sign-in failed: " + detail.splitlines()[0],
                         EXIT_CONFIG)

    return response.json()["access_token"]


def graph_request(session, method, url, body=None):
    """Call Graph with retries for 429/5xx and clear messages for errors."""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.request(method, url, json=body,
                                       timeout=REQUEST_TIMEOUT)
        except requests.RequestException as error:
            if attempt == MAX_RETRIES:
                raise GraphError(f"Network error calling Graph: {error}",
                                 EXIT_TEMPORARY) from error
            log.warning("Network error, retrying (%d/%d)", attempt,
                        MAX_RETRIES)
            time.sleep(2 * attempt)
            continue

        status = response.status_code
        if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
            wait = _retry_after(response, default=5 * attempt)
            log.warning("Graph returned %d, waiting %ds (%d/%d)",
                        status, wait, attempt, MAX_RETRIES)
            time.sleep(wait)
            continue

        if status < 400:
            return _json_or_empty(response)

        raise _error_for(response)

    raise GraphError("Graph kept failing, try again later", EXIT_TEMPORARY)


def _error_for(response):
    """Turn a failed Graph response into a GraphError with a clear message."""

    status = response.status_code
    error = _json_or_empty(response).get("error", {})
    graph_message = error.get("message", response.text[:300]).splitlines()[0]
    request_id = response.headers.get("request-id", "-")

    if status == 401:
        hint = "token rejected: check tenant ID / client ID / secret expiry"
        code = EXIT_CONFIG
    elif status == 403:
        hint = ("permission missing: the app needs "
                "DeviceManagementConfiguration.ReadWrite.All (Application) "
                "with admin consent, and the tenant needs an Intune licence")
        code = EXIT_CONFIG
    elif status == 404:
        hint = "not found: check the policy ID / group ID"
        code = EXIT_REJECTED
    elif status == 400:
        hint = "Graph rejected the request body: check the JSON file"
        code = EXIT_REJECTED
    elif status in (429, 500, 502, 503, 504):
        hint = "Graph is throttling or unavailable: try again later"
        code = EXIT_TEMPORARY
    else:
        hint = "unexpected response"
        code = EXIT_REJECTED

    return GraphError(f"HTTP {status} - {hint}. Graph says: {graph_message} "
                      f"(request-id {request_id})", code)


def _retry_after(response, default):
    try:
        return max(1, min(int(response.headers.get("Retry-After", default)),
                          60))
    except ValueError:
        return default


def _json_or_empty(response):
    try:
        return response.json()
    except ValueError:
        return {}


def make_session(settings):
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {get_token(settings)}",
        "Content-Type": "application/json",
    })
    return session


def find_policy(session, display_name):
    """Return the live policy with this display name, or None."""

    url = POLICIES_URL
    matches = []
    while url:
        page = graph_request(session, "GET", url)
        matches += [p for p in page.get("value", [])
                    if p.get("displayName") == display_name]
        url = page.get("@odata.nextLink")

    if len(matches) > 1:
        raise GraphError(f"{len(matches)} policies are called "
                         f"'{display_name}' - rename or delete the extra ones "
                         "in the portal first", EXIT_REJECTED)
    return matches[0] if matches else None


# ---------------------------------------------------------------- policy JSON

def clean_policy(policy):
    """Keep only the settings that belong in Git (drop IDs, dates, nulls)."""

    cleaned = {}
    for key, value in policy.items():
        if value is None or key in READ_ONLY_FIELDS:
            continue
        if key.startswith("@odata.") and key != "@odata.type":
            continue
        cleaned[key] = value
    return cleaned


def read_policy_file(path):
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GraphError(f"Policy file not found: {path} "
                         "(run 'export' first)", EXIT_CONFIG) from error
    except json.JSONDecodeError as error:
        raise GraphError(f"Policy file is not valid JSON: {error}",
                         EXIT_CONFIG) from error

    for field in ("@odata.type", "displayName"):
        if not policy.get(field):
            raise GraphError(f"Policy file has no '{field}'", EXIT_CONFIG)
    return policy


# ---------------------------------------------------------------- modes

def do_export(settings, display_name):
    session = make_session(settings)
    policy = find_policy(session, display_name)
    if policy is None:
        raise GraphError(f"No policy called '{display_name}' in Intune",
                         EXIT_REJECTED)

    path = settings["KH_POLICY_FILE"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_policy(policy), indent=2) + "\n",
                    encoding="utf-8")
    log.info("Exported '%s' (id %s) to %s", display_name, policy["id"], path)


def do_apply(settings, dry_run):
    wanted = read_policy_file(settings["KH_POLICY_FILE"])
    name = wanted["displayName"]
    group_id = settings["KH_ASSIGN_GROUP_ID"]
    assignment = {"assignments": [{
        "target": {
            "@odata.type": "#microsoft.graph.groupAssignmentTarget",
            "groupId": group_id,
        }
    }]}

    session = make_session(settings)
    live = find_policy(session, name)

    if dry_run:
        action = "create" if live is None else f"update (id {live['id']})"
        log.info("[dry run] would %s '%s' and assign it to group %s only",
                 action, name, group_id)
        return

    if live is None:
        body = dict(wanted)
        body.setdefault("scheduledActionsForRule", DEFAULT_ACTIONS)
        created = graph_request(session, "POST", POLICIES_URL, body)
        policy_id = created["id"]
        log.info("Created '%s' (id %s)", name, policy_id)
    else:
        policy_id = live["id"]
        body = {key: value for key, value in wanted.items()
                if key != "scheduledActionsForRule"}
        graph_request(session, "PATCH", f"{POLICIES_URL}/{policy_id}", body)
        log.info("Updated '%s' (id %s) from %s", name, policy_id,
                 settings["KH_POLICY_FILE"].name)

    # assign replaces ALL assignments: include the group only, no exclusions
    graph_request(session, "POST", f"{POLICIES_URL}/{policy_id}/assign",
                  assignment)
    log.info("Assignment set: include group %s only (no exclusions)",
             group_id)
    print_assignments(session, policy_id)


def do_show(settings, display_name):
    session = make_session(settings)
    policy = find_policy(session, display_name)
    if policy is None:
        raise GraphError(f"No policy called '{display_name}' in Intune",
                         EXIT_REJECTED)
    print_assignments(session, policy["id"])


def print_assignments(session, policy_id):
    result = graph_request(session, "GET",
                           f"{POLICIES_URL}/{policy_id}/assignments")
    rows = result.get("value", [])
    if not rows:
        print("No assignments (policy does nothing).")
    for row in rows:
        target = row.get("target", {})
        kind = target.get("@odata.type", "").split(".")[-1]
        label = {
            "groupAssignmentTarget": "Included group",
            "exclusionGroupAssignmentTarget": "EXCLUDED group",
            "allDevicesAssignmentTarget": "All devices",
            "allLicensedUsersAssignmentTarget": "All users",
        }.get(kind, kind)
        print(f"  {label}: {target.get('groupId', '-')}")


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export / apply the Intune compliance policy via Graph.")
    parser.add_argument("mode", choices=["export", "apply", "show"])
    parser.add_argument("--name", default="cp-kh-windows-baseline",
                        help="policy display name (export/show)")
    parser.add_argument("--dry-run", action="store_true",
                        help="apply: only say what would change")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    try:
        settings = load_settings(need_group=args.mode == "apply")
        if args.mode == "export":
            do_export(settings, args.name)
        elif args.mode == "apply":
            do_apply(settings, args.dry_run)
        else:
            do_show(settings, args.name)
    except GraphError as error:
        log.error("%s", error)
        return error.exit_code

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
