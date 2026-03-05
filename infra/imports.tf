# One-time imports for pre-existing resources.
# These are no-ops after the first successful apply — safe to delete after.

import {
  to = module.secrets.google_secret_manager_secret.hd_wallet_seed
  id = "projects/agent-registry-488317/secrets/hd_wallet_master_seed"
}

import {
  to = module.secrets.google_secret_manager_secret.treasury_wallet_key
  id = "projects/agent-registry-488317/secrets/treasury_wallet_private_key"
}

import {
  to = module.secrets.google_secret_manager_secret.admin_api_keys
  id = "projects/agent-registry-488317/secrets/admin-api-keys-${var.environment}"
}

import {
  to = module.secrets.google_secret_manager_secret.admin_path_prefix
  id = "projects/agent-registry-488317/secrets/admin-path-prefix-${var.environment}"
}
