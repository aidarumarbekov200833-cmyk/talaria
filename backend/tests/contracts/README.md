# Backend contract gates

Committed manifests: `openapi-contract.json`, `alembic-contract.json`, and
`error-envelope-contract.json`. Run the complete DOM/action/OpenAPI/Alembic/
error-envelope gate with Node and Python on `PATH` (CI: setup-node and the
workspace-venv/bootstrap):

```powershell
pnpm.cmd --dir frontend run test:contracts
```

Capture an approved OpenAPI or Alembic change from repository root:

```powershell
.venv\Scripts\python.exe scripts/contract_gate.py openapi capture --app backend.app.main:app --output backend/tests/contracts/openapi-contract.json
.venv\Scripts\python.exe scripts/contract_gate.py alembic capture --versions backend/alembic/versions --output backend/tests/contracts/alembic-contract.json
```
