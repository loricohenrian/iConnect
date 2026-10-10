# Project deployment workflow

- The user wants completed implementation changes deployed to the Orange Pi by
  default, without a separate request to deploy each change. This does not
  authorize changes when the request is only to inspect, diagnose, or explain.
- Verify changes, commit only task-related files, and push to the existing remote
  before deploying the verified commit. Preserve unrelated local changes.
- The production checkout is `/opt/iconnect/pisowifi`, reachable through the
  configured SSH alias `orangepi`. Do not copy credentials into this repository.
- Check production status and preserve affected files before deployment. Use a
  fast-forward update; never discard production-local edits to force a pull.
- For compatible web-only changes, gracefully reload `pisowifi` rather than
  stopping the coin detector or session timer workers. Do not alter customer
  sessions, database schema, network rules, or shutdown schedules incidentally.
- Flag disruptive maintenance or any new decision before proceeding. If access,
  safety approval, or deployment verification fails, report the actual state;
  never claim an undeployed or unverified change is live.
- Verify the relevant live output and service health, then state that deployment
  is complete. Do not include unrelated deployments or historical credit changes.
