from __future__ import annotations


def print_apex_owner_not_configured(app_id: str, owner: str, environment: str) -> None:
    print(
        f"\nAPP {app_id} is owned by schema {owner}, which is not configured "
        f"for environment {environment}.\n"
        f"Add {owner} to your connections to export it; skipping APP {app_id}."
    )


def print_apex_app_not_found(app_id: str) -> None:
    print(f"\nAPP {app_id} was not found in any configured APEX schema.")
