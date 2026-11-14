#!/usr/bin/env python3
"""Prepare an isolated Docker observability module; never start containers."""
from __future__ import annotations
import argparse,hashlib,ipaddress,json,os,re,secrets,ssl,sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.product_paths import require_product_path

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--bind-ip',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=19444)
    parser.add_argument('--grafana-image',required=True)
    parser.add_argument('--prometheus-image',required=True)
    parser.add_argument('--nginx-image',required=True)
    parser.add_argument('--server-cert',required=True,type=Path)
    parser.add_argument('--server-key',required=True,type=Path)
    parser.add_argument('--node-exporter',action='append',default=[],metavar='HOST:PORT')
    parser.add_argument('--npu-exporter',action='append',default=[],metavar='HOST:PORT')
    parser.add_argument('--native-targets-file',type=Path)
    args=parser.parse_args()
    if any(ord(c)<32 or c in "'\"$`" for c in str(args.root)):
        raise SystemExit('Installation directory contains unsafe characters')
    root=require_product_path(args.root.expanduser(),'Observability installation directory')
    if root.exists():
        raise SystemExit('--root must be an absolute directory that does not exist')
    if any(p.is_symlink() for p in root.parents):
        raise SystemExit('Refuse symlinked installation parents')
    origin=urlsplit(args.origin)
    if origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.path not in ('','/') or origin.query or origin.fragment:
        raise SystemExit('--origin must be an exact HTTPS origin')
    if not 1024<=args.port<=65535 or (origin.port or 443)!=args.port:
        raise SystemExit('HTTPS origin and published port must match')
    ipaddress.ip_address(args.bind_ip)
    for image in (args.grafana_image,args.prometheus_image,args.nginx_image):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
            raise SystemExit('Use verified immutable local image IDs, not moving tags')
    for path in (args.server_cert,args.server_key):
        if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):
            raise SystemExit('Server TLS files must be regular files without symlink parents')
    if 'PRIVATE KEY' not in args.server_key.read_text(encoding='ascii'):
        raise SystemExit('Invalid server private-key PEM')
    if re.search(r'(^|[-_.])ca([-_.]|$)',args.server_key.stem,re.I):
        raise SystemExit('Use only the server key; never a CA private key')
    # Validate the certificate/key pair before creating any installation files.
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(args.server_cert),str(args.server_key))
    web=Path(__file__).resolve().parents[1]
    native=web.parent/'mindie-motor/upstream/examples/features/observability/stack/grafana/dashboards/motor-all-metrics.json'
    if not native.is_file():
        raise SystemExit('Fixed native Motor dashboard is missing')
    native_data=json.loads(native.read_text(encoding='utf-8'))
    if native_data.get('uid')!='motor-all-metrics':
        raise SystemExit('Unexpected native dashboard identity')
    targets={}
    for kind,values in (('node',args.node_exporter),('npu',args.npu_exporter)):
        entries=[]
        if len(values)>64:
            raise SystemExit('At most 64 exporter targets of each kind are supported')
        for raw in values:
            if any(ord(c)<32 for c in raw):
                raise SystemExit('Exporter target contains control characters')
            parsed=urlsplit('//'+raw)
            if not parsed.hostname or not parsed.port or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise SystemExit('Exporter target must be HOST:PORT')
            host=parsed.hostname
            if not re.fullmatch(r'[A-Za-z0-9.-]+',host) or not 1<=parsed.port<=65535:
                raise SystemExit('Invalid exporter host/port')
            entries.append({'targets':[raw],'labels':{'node':host,'cluster':'qinghuan'}})
        if len(set(values))!=len(values):
            raise SystemExit('Duplicate exporter target')
        targets[kind]=entries
    if args.native_targets_file and args.native_targets_file.stat().st_size>65536:
        raise SystemExit('Native target configuration exceeds 64 KiB')
    native_targets=json.loads(args.native_targets_file.read_text(encoding='utf-8')) if args.native_targets_file else []
    if not isinstance(native_targets,list) or len(native_targets)>128:
        raise SystemExit('Native targets must be a bounded Prometheus file_sd list')
    target_count=0
    for item in native_targets:
        if not isinstance(item,dict) or not isinstance(item.get('targets'),list) or not isinstance(item.get('labels',{}),dict):
            raise SystemExit('Invalid native target group')
        if not 1<=len(item['targets'])<=32 or len(item.get('labels',{}))>32:
            raise SystemExit('Native target group exceeds target or label budget')
        target_count+=len(item['targets'])
        if target_count>256:
            raise SystemExit('Native targets exceed the 256-target budget')
        for target in item['targets']:
            if not isinstance(target,str) or any(ord(c)<32 for c in target):
                raise SystemExit('Invalid native target value')
            parsed=urlsplit('//'+target)
            if not parsed.hostname or not parsed.port or parsed.path or parsed.username or parsed.password:
                raise SystemExit('Invalid native metrics target')
    root.mkdir(parents=True,mode=0o750)
    os.chmod(root,0o750)
    def directory(relative:str,mode:int=0o755,owner:tuple[int,int]|None=None)->Path:
        path=root/relative;path.mkdir(parents=True,exist_ok=True,mode=mode);os.chmod(path,mode)
        if owner and os.name=='posix':os.chown(path,*owner)
        return path
    def write(relative:str,body:str|bytes,mode:int=0o644,owner:tuple[int,int]|None=None)->Path:
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(body.encode('utf-8') if isinstance(body,str) else body)
        os.chmod(path,mode)
        if owner and os.name=='posix':os.chown(path,*owner)
        return path
    def asjson(value:object)->str:return json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    directory('config/prometheus/targets')
    directory('config/grafana/provisioning/datasources')
    directory('config/grafana/provisioning/dashboards')
    directory('config/grafana/dashboards')
    directory('private',0o711)
    directory('private/grafana',0o700,(472,472))
    directory('private/tls',0o750,(0,101))
    directory('data',0o755)
    directory('data/prometheus',0o750,(65534,65534))
    directory('data/grafana',0o750,(472,472))
    admin_password=secrets.token_urlsafe(32)
    write('private/grafana/admin-password',admin_password+'\n',0o400,(472,472))
    write('private/grafana/secret-key',secrets.token_urlsafe(48)+'\n',0o400,(472,472))
    write('private/tls/server.crt',args.server_cert.read_bytes(),0o444,(0,101))
    write('private/tls/server.key',args.server_key.read_bytes(),0o440,(0,101))
    write('private/access.json',asjson({'origin':args.origin.rstrip('/'),'username':'admin','password':admin_password,'viewer_service_token_created':False}),0o600)
    write('compose.yaml',(web/'compose.observability.yaml').read_bytes())
    write('config/nginx.conf',(web/'observability/nginx.conf').read_bytes())
    scrape=[]
    for kind in ('node','npu'):
        write('config/prometheus/targets/'+kind+'.json',asjson(targets[kind]))
        # Large CPU hosts expose many series per family; keep the limit finite.
        sample_limit=50000 if kind=='node' else 20000
        scrape.append({'job_name':kind+'-exporter','scrape_interval':'30s','scrape_timeout':'10s','sample_limit':sample_limit,'label_limit':32,'body_size_limit':'8MB','file_sd_configs':[{'files':['/etc/prometheus/targets/'+kind+'.json'],'refresh_interval':'60s'}]})
    write('config/prometheus/targets/native.json',asjson(native_targets))
    scrape.append({'job_name':'motor-native','scrape_interval':'30s','scrape_timeout':'10s','sample_limit':30000,'label_limit':48,'body_size_limit':'16MB','file_sd_configs':[{'files':['/etc/prometheus/targets/native.json'],'refresh_interval':'60s'}]})
    write('config/prometheus/rules/host-npu.rules.yaml',(web/'observability/rules/host-npu.rules.yaml').read_bytes())
    write('config/prometheus/prometheus.yml',asjson({'global':{'scrape_interval':'30s','scrape_timeout':'10s','evaluation_interval':'30s'},'rule_files':['/etc/prometheus/rules/host-npu.rules.yaml'],'scrape_configs':scrape}))
    write('config/grafana/provisioning/datasources/prometheus.yaml',asjson({'apiVersion':1,'datasources':[{'name':'Prometheus','type':'prometheus','uid':'prometheus','access':'proxy','url':'http://prometheus:9090','isDefault':True,'editable':False,'jsonData':{'timeInterval':'30s','httpMethod':'POST','queryTimeout':'30s'}}]}))
    write('config/grafana/provisioning/dashboards/dashboards.yaml',asjson({'apiVersion':1,'providers':[{'name':'qinghuan-observability','orgId':1,'folder':'擎寰运行观测','type':'file','disableDeletion':False,'editable':False,'updateIntervalSeconds':60,'allowUiUpdates':False,'options':{'path':'/opt/qinghuan/dashboards','foldersFromFilesStructure':False}}]}))
    write('config/grafana/dashboards/motor-all-metrics.json',native.read_bytes())
    write('config/grafana/dashboards/host-npu.json',(web/'observability/dashboards/host-npu.json').read_bytes())
    write('config/grafana/dashboards/host-npu-node.json',(web/'observability/dashboards/host-npu-node.json').read_bytes())
    env={'QH_OBSERVABILITY_ROOT':str(root),'QH_GRAFANA_ORIGIN':args.origin.rstrip('/'),'QH_OBSERVABILITY_BIND_IP':args.bind_ip,'QH_OBSERVABILITY_PORT':str(args.port),'QH_GRAFANA_IMAGE':args.grafana_image,'QH_PROMETHEUS_IMAGE':args.prometheus_image,'QH_NGINX_IMAGE':args.nginx_image}
    write('.env.observability',''.join(k+'='+json.dumps(v)+'\n' for k,v in env.items()),0o600)
    files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file() and 'private' not in p.relative_to(root).parts}
    receipt={'prepared':True,'services_started':False,'project':'qinghuan-observability','origin':args.origin.rstrip('/'),'images':{'grafana':args.grafana_image,'prometheus':args.prometheus_image,'nginx':args.nginx_image},'targets':{k:len(v) for k,v in targets.items()},'native_target_groups':len(native_targets),'native_dashboard_sha256':digest(native),'files_sha256':files,'private_credentials':str(root/'private/access.json'),'limits':{'cpus':2,'memory_mib':1920,'retention':'7d / 1GB blocks; WAL/head additional'}}
    write('prepared.json',asjson(receipt),0o600)
    print(asjson({k:v for k,v in receipt.items() if k!='files_sha256'}),end='')

if __name__=='__main__':
    main()
