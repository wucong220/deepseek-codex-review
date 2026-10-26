// Optional DSH host adapter. Load only after the user authorizes this fixed review capability.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile, realpath} from 'node:fs/promises';
import {isAbsolute, join, relative, resolve, sep} from 'node:path';
import {pathToFileURL} from 'node:url';

export const name = 'codex-review-host-tool';
export const inject = ['tools'];

function inside(root, path) {
  const rel = relative(root, path);
  return rel !== '' && rel !== '..' && !rel.startsWith('..' + sep) && !isAbsolute(rel);
}

export async function validateTask(taskDir, cwd) {
  if (!cwd || !isAbsolute(cwd) || !isAbsolute(taskDir)) throw new Error('Absolute session cwd and task_dir required');
  const workspace = await realpath(cwd);
  const task = await realpath(taskDir);
  const allowed = await realpath(join(workspace, 'work', 'codex-review'));
  if (!inside(workspace, allowed)) throw new Error('Review root must stay inside this session workspace');
  if (!inside(allowed, task)) throw new Error('Task must be under this session workspace/work/codex-review');
  const state = JSON.parse(await readFile(join(task, 'state.json'), 'utf8'));
  if (![1,2].includes(state.format) || !isAbsolute(state.project ?? '') || !isAbsolute(state.task_dir ?? '') ||
      await realpath(state.project) !== workspace || await realpath(state.task_dir) !== task) {
    throw new Error('Task state does not belong to this session workspace');
  }
  return {workspace, task};
}

export async function apply(ctx, config = {}) {
  if (config.authorizedHostReview !== true) throw new Error('Explicit user authorization required before loading host review tool');
  for (const key of ['dshRoot', 'pythonExe', 'reviewScript']) {
    if (!isAbsolute(config[key] ?? '')) throw new Error(`Absolute ${key} required`);
  }
  if (!/^[0-9a-f]{64}$/.test(config.scriptSha256 ?? '')) throw new Error('Pinned script SHA-256 required');
  const {defineTool} = await import(pathToFileURL(join(config.dshRoot, 'node_modules/@deepseek-ai/dsh-tools/lib/index.js')).href);
  ctx.tools.register(defineTool({
    name: 'codex_review',
    description: 'Run the user-authorized fixed Codex review helper for this workspace: plan (review a proposed plan), accept (acceptance after an approved plan), review (directly review an existing project without PLAN/PASS), status or delivery verification. Codex keeps its own read-only sandbox and default model/effort. No arbitrary shell commands. Initialize task materials using the deepseek-codex-review Skill first.',
    parameters: {
      action: {type:'string', required:true, enum:['plan','accept','review','status','verify']},
      task_dir: {type:'string', required:true, description:'Absolute existing task directory under this session workspace/work/codex-review'},
      images: {type:'array', items:{type:'string'}, description:'Optional absolute image paths inside this workspace'},
    },
    output: {
      schema: {type:'object', properties:{exit_code:{type:'integer',required:true},output:{type:'string',required:true},diagnostics:{type:'string',required:true}},additionalProperties:false},
      render: (_args, value) => [{type:'text',text:JSON.stringify(value)}],
    },
    async execute(args, exec) {
      exec.signal.throwIfAborted();
      if (!['plan','accept','review','status','verify'].includes(args.action)) throw new Error('Unsupported action');
      const {workspace, task} = await validateTask(args.task_dir, exec.agent?.session.header.cwd);
      const script = await realpath(config.reviewScript);
      const hash = createHash('sha256').update(await readFile(script)).digest('hex');
      if (hash !== config.scriptSha256) throw new Error('Review script changed; inspect and update trusted hash before running');
      const command = [script, args.action, '--task', task];
      for (const image of args.images ?? []) {
        const file = await realpath(image);
        if (!isAbsolute(image) || !inside(workspace, file)) throw new Error('Images must be inside this workspace');
        if (['status','verify'].includes(args.action)) throw new Error('status and verify do not accept images');
        command.push('--image', file);
      }
      exec.signal.throwIfAborted();
      return await new Promise((resolvePromise, reject) => {
        const child = spawn(config.pythonExe, command, {cwd:workspace, shell:false, windowsHide:true, stdio:['ignore','pipe','pipe'], detached:process.platform!=='win32'});
        child.stdout.setEncoding('utf8');
        child.stderr.setEncoding('utf8');
        let output = '', diagnostics = '', cancelled = false;
        const stop = () => {
          cancelled = true;
          if (!child.pid || child.exitCode !== null) return;
          if (process.platform === 'win32') spawn('taskkill', ['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
          else { try { process.kill(-child.pid,'SIGTERM'); } catch {} }
        };
        exec.signal.addEventListener('abort', stop, {once:true});
        if (exec.signal.aborted) stop();
        child.stdout.on('data',chunk=>{output+=chunk; if(output.length>2_000_000) stop();});
        child.stderr.on('data',chunk=>{diagnostics=(diagnostics+chunk).slice(-16000);});
        child.on('error',error=>{exec.signal.removeEventListener('abort',stop);reject(error);});
        child.on('close',code=>{
          exec.signal.removeEventListener('abort',stop);
          if (cancelled) reject(new Error('Review cancelled; verify process exit and task lock before retrying'));
          else resolvePromise({exit_code:code ?? 12,output,diagnostics});
        });
      });
    },
  }));
}
