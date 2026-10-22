// Offline boundary tests; the optional integration uses an already installed DSH.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, writeFile, readFile, realpath, symlink, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {basename, dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {apply, validateTask} from '../scripts/dsh-host-tool.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

async function fixture(t) {
  const parent = await realpath(tmpdir());
  const base = await mkdtemp(join(parent, 'dsh-review-host-test-'));
  t.after(async () => {
    const checked = await realpath(base);
    assert.equal(dirname(checked), parent);
    assert.ok(basename(checked).startsWith('dsh-review-host-test-'));
    await rm(checked, {recursive:true, force:true});
  });
  const a = join(base, 'a'), b = join(base, 'b');
  const task = join(a, 'work/codex-review/test');
  await mkdir(task, {recursive:true});
  await mkdir(join(b, 'work/codex-review'), {recursive:true});
  const state = {format:2, task_id:'test', project:a, task_dir:task, runs:[], approved_plan:null, thread_id:null};
  const save = async value => writeFile(join(task, 'state.json'), JSON.stringify(value));
  await save(state);
  return {base, a, b, task, state, save};
}

test('accepts current task and recognizes legacy history', async t => {
  const {a, task, state, save} = await fixture(t);
  assert.equal((await validateTask(task, a)).task, await realpath(task));
  await save({...state, format:1});
  assert.equal((await validateTask(task, a)).task, await realpath(task));
});

test('rejects cross-workspace and relative inputs', async t => {
  const {a, b, task} = await fixture(t);
  await assert.rejects(validateTask(task, b), /this session/);
  await assert.rejects(validateTask('relative-task', a), /Absolute/);
  await assert.rejects(validateTask(task, '.'), /Absolute/);
});

test('rejects forged ownership and unknown formats', async t => {
  const {a, b, task, state, save} = await fixture(t);
  for (const mutation of [{project:b}, {task_dir:b}, {format:999}, {project:'.'}]) {
    await save({...state, ...mutation});
    await assert.rejects(validateTask(task, a), /does not belong/);
  }
});

test('rejects a review root redirected outside the workspace', async t => {
  const {a, b, task, state} = await fixture(t);
  const redirected = join(a, 'redirected');
  await mkdir(join(redirected, 'work'), {recursive:true});
  await symlink(join(b, 'work/codex-review'), join(redirected, 'work/codex-review'),
                process.platform === 'win32' ? 'junction' : 'dir');
  const outsideTask = join(b, 'work/codex-review/test');
  await mkdir(outsideTask);
  await writeFile(join(outsideTask, 'state.json'), JSON.stringify({...state, project:redirected, task_dir:outsideTask}));
  await assert.rejects(validateTask(outsideTask, redirected), /root must stay inside/);
});

test('requires explicit host authorization and pinned absolute configuration', async () => {
  await assert.rejects(apply({}, {}), /authorization/);
  await assert.rejects(apply({}, {authorizedHostReview:true}), /Absolute/);
  await assert.rejects(apply({}, {authorizedHostReview:true, dshRoot:root, pythonExe:root,
                               reviewScript:root, scriptSha256:'invalid'}), /Pinned/);
});

test('real DSH registration, fixed helper, action and hash boundaries', {
  skip: !process.env.DSH_PACKAGE_ROOT || !process.env.PYTHON_EXE,
}, async t => {
  const {a, task} = await fixture(t);
  let tool;
  const ctx = {tools:{register(value){tool=value;}}};
  const script = join(root, 'scripts/review.py');
  const config = {authorizedHostReview:true, dshRoot:process.env.DSH_PACKAGE_ROOT,
    pythonExe:process.env.PYTHON_EXE, reviewScript:script, scriptSha256:'0'.repeat(64)};
  await apply(ctx, config);
  assert.equal(tool.name, 'codex_review');
  const exec = {signal:new AbortController().signal, agent:{session:{header:{cwd:a}}}};
  await assert.rejects(tool.execute({action:'arbitrary-command', task_dir:task}, exec), /Unsupported action|must be one of/);
  await assert.rejects(tool.execute({action:'plan', task_dir:task}, exec), /script changed/);
  config.scriptSha256 = createHash('sha256').update(await readFile(script)).digest('hex');
  await apply(ctx, config);
  const status = await tool.execute({action:'status', task_dir:task}, exec);
  assert.equal(status.exit_code, 0);
  assert.equal(JSON.parse(status.output).current_acceptance_valid, false);
  const verification = await tool.execute({action:'verify', task_dir:task}, exec);
  assert.equal(verification.exit_code, 10);
  assert.equal(JSON.parse(verification.output).status, 'STALE');
});
