-- The billing day timezone, fixed when `relay migrate` first runs. One row, written by the
-- owner role only; both service roles read it, so a charge never depends on a process environment.
CREATE TABLE billing_pin (
    only_row  boolean PRIMARY KEY DEFAULT true CHECK (only_row),
    tz        text NOT NULL,
    pinned_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
GRANT SELECT ON billing_pin TO relay_api, relay_worker;
