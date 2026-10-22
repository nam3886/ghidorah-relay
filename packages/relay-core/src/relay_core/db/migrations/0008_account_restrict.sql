-- An account can be deleted only while it is unused (is the same rule for keys). Referential actions run
-- as the table owner, so a CASCADE or SET NULL from `accounts` would let relay_api, which may DELETE an account,
-- remove leases, capacity, account charges, shots and commands, or strip the account from a live job.
-- RESTRICT makes the delete fail at the database; a used account is disabled instead.
ALTER TABLE account_capacity DROP CONSTRAINT account_capacity_account_id_fkey;
ALTER TABLE account_capacity ADD CONSTRAINT account_capacity_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;
ALTER TABLE account_charges DROP CONSTRAINT account_charges_account_id_fkey;
ALTER TABLE account_charges ADD CONSTRAINT account_charges_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;
ALTER TABLE leases DROP CONSTRAINT leases_account_id_fkey;
ALTER TABLE leases ADD CONSTRAINT leases_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;
ALTER TABLE account_shots DROP CONSTRAINT account_shots_account_id_fkey;
ALTER TABLE account_shots ADD CONSTRAINT account_shots_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;
ALTER TABLE commands DROP CONSTRAINT commands_account_id_fkey;
ALTER TABLE commands ADD CONSTRAINT commands_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;
ALTER TABLE jobs DROP CONSTRAINT jobs_account_fk;
ALTER TABLE jobs ADD CONSTRAINT jobs_account_fk
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT;

-- relay_api updates an account's label, proxy and state only. Probe results, strikes, cooldown
-- and the check schedule belong to the worker; the admin acts on them through commands.
REVOKE UPDATE (proxy_ok, proxy_checked_at, strikes, cooldown_until, cooldown_reason, check_due_at)
    ON accounts FROM relay_api;
