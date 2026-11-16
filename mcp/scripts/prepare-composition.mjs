#!/usr/bin/env node
/** Offline preparation only. Artifacts are validated and pinned without importing plugin code. */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { generation, packageDigest, parseComposition, planDocument } from '@mindie/console-platform/host';

const allowed = new Set(['transport', 'output', 'registry-file', 'control-origin', 'allow-insecure-control', 'public-origin', 'tls-directory', 'stdio-credential-id', 'toolsets']);
const args = {};
try {
  const words = process.argv.slice(2);
  for (let index = 0; index < words.length; index += 2) {
    const flag = words[index]; const value = words[index + 1];
    if (!flag?.startsWith('--') || !allowed.has(flag.slice(2)) || !value || value.startsWith('--') || Object.hasOwn(args, flag.slice(2))) throw new Error('Unknown, duplicate or incomplete option');
    args[flag.slice(2)] = value;
  }
  const transport = args.transport ?? 'http';
  if (!['http', 'stdio', 'loopback-http'].includes(transport)) throw new Error('transport must be http, stdio or loopback-http');
  for (const key of ['output', 'registry-file', 'control-origin']) if (!args[key]) throw new Error(`Missing --${key}`);
  for (const key of ['output', 'registry-file']) if (!isAbsolute(args[key])) throw new Error(`${key} must be an absolute path in the runtime namespace`);
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const composition = JSON.parse(await readFile(resolve(root, 'examples', `${transport === 'loopback-http' ? 'loopback-http' : transport}.template.json`), 'utf8'));
  composition.settings['reference.mcp.auth'].registryFile = args['registry-file'];
  const control = new URL(args['control-origin']);
  if (!['http:', 'https:'].includes(control.protocol) || control.username || control.password || control.pathname !== '/' || control.search || control.hash) throw new Error('control-origin must be a fixed HTTP(S) origin');
  if (args['allow-insecure-control'] !== undefined && args['allow-insecure-control'] !== 'true') throw new Error('allow-insecure-control accepts only true');
  const plainRemoteControl = control.protocol === 'http:' && !['127.0.0.1', 'localhost', '[::1]'].includes(control.hostname);
  if (plainRemoteControl && args['allow-insecure-control'] !== 'true') throw new Error('Remote plaintext Control requires explicit --allow-insecure-control true on an isolated network');
  composition.settings['reference.mcp.control-client'].baseUrl = control.origin;
  composition.settings['reference.mcp.control-client'].allowInsecureHttp = plainRemoteControl;
  if (transport === 'http') {
    if (!args['public-origin'] || !args['tls-directory'] || !isAbsolute(args['tls-directory'])) throw new Error('HTTP requires --public-origin https://... and absolute --tls-directory');
    const origin = new URL(args['public-origin']);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw new Error('Production public-origin must be an HTTPS origin');
    Object.assign(composition.settings['reference.mcp.server'], { allowedHosts: [origin.hostname], allowedOrigins: [origin.origin], tlsCertFile: resolve(args['tls-directory'], 'server.crt'), tlsKeyFile: resolve(args['tls-directory'], 'server.key') });
  } else if (transport === 'stdio') {
    if (!args['stdio-credential-id']) throw new Error('stdio requires --stdio-credential-id');
    composition.settings['reference.mcp.auth'].stdioCredentialId = args['stdio-credential-id'];
  } else if (args['public-origin'] || args['tls-directory']) throw new Error('loopback-http uses its explicit local development origin');
  const domains = (args.toolsets ?? 'query,configuration,operations').split(',');
  if (!domains.length || new Set(domains).size !== domains.length || domains.some(domain => !['query', 'configuration', 'operations'].includes(domain))) throw new Error('toolsets must select query,configuration,operations without duplicates');
  const selectedTools = [...(domains.includes('query') ? ['reference.mcp.inventory', 'reference.mcp.services'] : []), ...(domains.includes('configuration') ? ['reference.mcp.configuration'] : []), ...(domains.includes('operations') ? ['reference.mcp.operations'] : [])];
  composition.bindings['console.mcp.toolsets'] = selectedTools;
  composition.selected = ['reference.mcp.server', 'reference.mcp.auth', 'reference.mcp.control-client', ...selectedTools];
  for (const artifact of composition.packages) {
    artifact.root = resolve(root, 'examples', artifact.root);
    artifact.sha256 = await packageDigest(artifact.root);
  }
  const parsed = parseComposition(composition);
  const document = { file: resolve(args.output), composition: parsed, generation: generation(parsed) };
  await planDocument(document);
  await writeFile(document.file, JSON.stringify(parsed, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  process.stdout.write(JSON.stringify({ output: document.file, generation: document.generation, codeExecuted: false, restartRequired: true }) + '\n');
} catch (error) {
  process.stderr.write(JSON.stringify({ error: error instanceof Error ? error.message : 'Composition preparation failed', codeExecuted: false }) + '\n');
  process.exitCode = 1;
}
