import { existsSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pythonCandidates = process.platform === 'win32'
  ? [resolve(root, '.venv', 'Scripts', 'python.exe'), 'python']
  : [resolve(root, '.venv', 'bin', 'python'), 'python3', 'python'];
const python = pythonCandidates.find(candidate => candidate === 'python' || candidate === 'python3' || existsSync(candidate));
const jsSources = readdirSync(resolve(root, 'frontend', 'js'))
  .filter(name => name.endsWith('.js'))
  .flatMap(name => ['--source', `frontend/js/${name}`]);
const commands = [
  { args: ['scripts/contract_gate.py', 'dom', 'check', '--manifest', 'frontend/tests/contracts/dom-contract.json', '--html', 'frontend/index.html'] },
  { args: ['scripts/contract_gate.py', 'actions', 'check', '--manifest', 'frontend/tests/contracts/actions-contract.json', '--html', 'frontend/index.html', ...jsSources] },
  { args: ['scripts/contract_gate.py', 'openapi', 'check', '--app', 'backend.app.main:app', '--manifest', 'backend/tests/contracts/openapi-contract.json'] },
  { args: ['scripts/contract_gate.py', 'alembic', 'check', '--versions', 'backend/alembic/versions', '--manifest', 'backend/tests/contracts/alembic-contract.json'] },
  { args: ['-m', 'pytest', 'tests/contracts/test_error_envelope_contract.py'], cwd: resolve(root, 'backend') },
];

for (const command of commands) {
  const result = spawnSync(python, command.args, { cwd: command.cwd ?? root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
