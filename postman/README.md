# PlanLab Postman collection

Lets judges and teammates drive PlanLab without the UI.

1. Postman > Import `PlanLab.postman_collection.json` and `PlanLab.postman_environment.json`.
2. Select the **PlanLab** environment and set `baseUrl` (local: `http://localhost:8000`, or the Render URL).
3. Open **Demo flow (run in order)** > Run. It designs an experiment, runs the labelled guardrail
   scenario, lets the agent pause the risky arm, asks *why*, runs the Save Agent and imports a
   sample 12-month history. Each step saves the ids the next one needs.

The other folders hold every API route, generated from the OpenAPI schema. Regenerate after
changing the API:

    cd backend && python scripts/export_postman.py

`backend/tests/test_postman.py` replays the demo flow on every test run.

To publish: create a public workspace in Postman, import both files, and put the workspace link in
the README and on Devpost.
