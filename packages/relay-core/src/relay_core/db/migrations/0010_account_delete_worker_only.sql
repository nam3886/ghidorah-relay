-- Deleting an account is a worker action (it must also remove the screenshot objects): the api keeps no DELETE on
-- accounts. The worker already holds what `delete_unused_account` needs (accounts, account_capacity,
-- account_shots, commands, leases).
REVOKE DELETE ON accounts FROM relay_api;
