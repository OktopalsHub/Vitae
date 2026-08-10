"""Per-user account helpers (B2C — profiles with per-profile entitlements)."""

from app.accounts.bootstrap import ensure_account, ensure_profile_billing
from app.accounts.billing_access import (
    can_open_listing,
    can_use_ai,
    detect_billing_region,
    detect_billing_region_detail,
    free_clear_listing_ids,
    has_full_job_access,
    llm_creds_for_user,
    FREE_CLEAR_MATCHES,
)
from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db
from app.accounts.paths import profile_data_dir, user_data_dir
from app.accounts.profile import (
    apply_profile_chips,
    apply_profile_from_user,
    archive_profile,
    create_profile,
    get_active_profile,
    get_profile_billing,
    is_profile_confirmed,
    list_user_profiles,
    load_user_profile_dict,
    rename_profile,
    switch_active_profile,
)
from app.accounts.settings import (
    get_preview_listing_id,
    load_user_settings,
    save_user_settings,
    set_preview_listing_id,
)

__all__ = [
    "ensure_account",
    "ensure_profile_billing",
    "user_data_dir",
    "profile_data_dir",
    "get_active_profile",
    "get_profile_billing",
    "list_user_profiles",
    "create_profile",
    "switch_active_profile",
    "rename_profile",
    "archive_profile",
    "load_user_profile_dict",
    "apply_profile_from_user",
    "apply_profile_chips",
    "is_profile_confirmed",
    "load_user_settings",
    "save_user_settings",
    "get_preview_listing_id",
    "set_preview_listing_id",
    "can_use_ai",
    "has_full_job_access",
    "can_open_listing",
    "free_clear_listing_ids",
    "FREE_CLEAR_MATCHES",
    "llm_creds_for_user",
    "detect_billing_region",
    "detect_billing_region_detail",
    "load_apply_draft_db",
    "save_apply_draft_db",
]
