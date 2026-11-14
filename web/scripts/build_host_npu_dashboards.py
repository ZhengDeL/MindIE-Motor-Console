#!/usr/bin/env python3
"""Build the provisioned fleet and node dashboards without contacting any service."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

DS = {'type': 'prometheus', 'uid': 'prometheus'}
HOST = 'cluster,node'
DEVICE = 'cluster,node,id'
# Let the Prometheus datasource escape variables; generic :regex interpolation
# produces an invalid single backslash inside PromQL string literals for IPs.
SCOPE = 'cluster=~"$cluster",node=~"$node"'
UID = 'qinghuan-host-npu'
DETAIL_UID = UID + '-node'


def selector(metric: str, job: str, extra: str = '') -> str:
    return f'{metric}{{job="{job}",{SCOPE}{"," + extra if extra else ""}}}'


def fresh(sample: str) -> str:
    return f'((time() - timestamp({sample}) >= 0) and (time() - timestamp({sample}) < 90))'


def collector(job: str) -> str:
    sample = selector('up', job)
    return f'({sample} and {fresh(sample)})'


def valid(metric: str, job: str = 'npu-exporter', *, extra: str = '', minimum: int = 0, maximum: int = 2**53 - 1) -> str:
    sample = selector(metric, job, extra)
    bounds = f'({sample} >= {minimum}) and ({sample} <= {maximum})'
    if job == 'npu-exporter':
        # Ascend Exporter uses these values for failed device reads.
        bounds += f' and ({sample} != -1) and ({sample} != 4294967295) and ({sample} != 4294967296)'
    source = collector(job)
    # Do not pair complementary fields from different exporters for one host.
    sole_source = f'(count by(cluster,node,job) ({source} == 1) == 1)'
    return f'({sample} and ({bounds}) and {fresh(sample)} and on(cluster,job,instance) ({source} == 1) and on(cluster,node,job) {sole_source})'


def unique(expr: str, keys: str, labels: str = '') -> str:
    """Duplicate sources are unknown, rather than silently choosing their largest value."""
    output = keys + (',' + labels if labels else '')
    return f'(max by({output}) ({expr}) and on({keys}) (count by({keys}) ({expr}) == 1))'


def host(metric: str, *, labels: str = '', extra: str = '') -> str:
    return unique(valid(metric, 'node-exporter', extra=extra), HOST, labels)


def chip(metric: str, *, labels: str = '', minimum: int = 0, maximum: int = 2**53 - 1) -> str:
    return unique(valid('npu_chip_info_' + metric, minimum=minimum, maximum=maximum), DEVICE, labels)


def rate(metric: str, *, extra: str = '') -> str:
    sample = selector(metric, 'node-exporter', extra)
    return f'(rate({sample}[$__rate_interval]) and {valid(metric, "node-exporter", extra=extra)})'


CPU_COUNTER = selector('node_cpu_seconds_total', 'node-exporter', 'mode="idle"')
CPU_UP = collector('node-exporter')
# timestamp must receive the original selector: a derived vector would carry
# evaluation time instead of its source sample time. Long-range charts use the
# recording rule below, so validation can remain explicit and inexpensive at
# each short, fixed-window rule evaluation.
IDLE_RATE = f'(rate({CPU_COUNTER}[2m]) and {valid("node_cpu_seconds_total", "node-exporter", extra="mode=\"idle\"")})'
CPU_VALUE = f'(100 * (1 - avg by(cluster,node,instance) ({IDLE_RATE})))'
# valid() already requires one healthy exporter per host. Repeating this
# expensive per-core range expression for uniqueness and both bounds can exceed
# a lightweight Prometheus query budget on high-core-count fleets. Non-bool
# chained comparisons filter invalid values while evaluating the rate once.
CPU = f'max by({HOST}) (({CPU_VALUE} >= 0) <= 100)'
CPU_RECORD_NAME = 'qinghuan:host_cpu_busy_percent:rate2m'
CPU_RECORDED_SAMPLE = f'{CPU_RECORD_NAME}{{{SCOPE}}}'
CPU_HISTORY = (f'(({CPU_RECORDED_SAMPLE} >= 0) <= 100) and {fresh(CPU_RECORDED_SAMPLE)}'
               f' and on({HOST}) (max by({HOST}) ({CPU_UP}) == 1)'
               f' and on({HOST}) (count by({HOST}) ({CPU_UP} == 1) == 1)')
MEM_TOTAL = f'({host("node_memory_MemTotal_bytes")} > 0)'
MEM_AVAIL = f'({host("node_memory_MemAvailable_bytes")} <= on({HOST}) ({MEM_TOTAL} > 0))'
MEM_PAIRED_TOTAL = f'({MEM_TOTAL} and on({HOST}) {MEM_AVAIL})'
MEM = f'(100 * (1 - {MEM_AVAIL} / on({HOST}) {MEM_PAIRED_TOTAL}))'
IDENTITY = f'({chip("name", labels="name")} == 1)'
UTIL = chip('utilization', maximum=100)
HEALTH_RAW = chip('health_status', maximum=1)
HEALTH = f'(({HEALTH_RAW} == 0) or ({HEALTH_RAW} == 1))'
TEMP = chip('temperature', minimum=-40, maximum=200)
POWER = chip('power', maximum=10000)
HBM_TOTAL = f'({chip("hbm_total_memory")} > 0)'
HBM_USED = f'({chip("hbm_used_memory")} <= on({DEVICE}) {HBM_TOTAL})'
HBM_PAIRED_TOTAL = f'({HBM_TOTAL} and on({DEVICE}) {HBM_USED})'
HBM = f'(100 * {HBM_USED} / on({DEVICE}) {HBM_PAIRED_TOTAL})'
PROCESSES = chip('process_info_num', maximum=1_000_000)
PROCESSES = f'({PROCESSES} == floor({PROCESSES}))'
HBM_TEMP = chip('hbm_temperature', minimum=-40, maximum=200)
HBM_BANDWIDTH = chip('hbm_bandwidth_utilization', maximum=100)
BOOT = host('node_boot_time_seconds')
UPTIME = f'(time() - ({BOOT} <= time()))'
IDLE_SAMPLE = valid('node_cpu_seconds_total', 'node-exporter', extra='mode="idle"')
CORES = unique(f'count by(cluster,node,instance) ({IDLE_SAMPLE})', HOST)


def override(name: str, **properties) -> dict:
    return {'matcher': {'id': 'byName', 'options': name},
            'properties': [{'id': key, 'value': value} for key, value in properties.items()]}


def target(expr: str, index: int, legend: str = '', table: bool = False, instant: bool = False) -> dict:
    return {'refId': chr(65 + index), 'expr': expr, 'legendFormat': legend,
            'format': 'table' if table else 'time_series', 'instant': instant, 'range': not instant}


class Panels:
    def __init__(self):
        self.panels: list[dict] = []
        self.next_id = 1

    def add(self, title: str, kind: str, x: int, y: int, w: int, h: int, description: str = '', **kwargs) -> dict:
        panel = {'id': self.next_id, 'title': title, 'type': kind,
                 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
                 'description': description, 'datasource': copy.deepcopy(DS), **kwargs}
        self.next_id += 1
        self.panels.append(panel)
        return panel

    def stat(self, title: str, expr: str, x: int, unit: str = 'none', description: str = '', decimals: int = 0):
        return self.add(title, 'stat', x, 0, 4, 4, description,
                        targets=[target(expr, 0, instant=True)],
                        fieldConfig={'defaults': {'unit': unit, 'decimals': decimals, 'noValue': '—',
                                                  'color': {'mode': 'fixed', 'fixedColor': 'text'}}, 'overrides': []},
                        options={'reduceOptions': {'calcs': ['last'], 'values': False},
                                 'colorMode': 'none', 'graphMode': 'none', 'justifyMode': 'left',
                                 'orientation': 'horizontal', 'wideLayout': True,
                                 'textMode': 'value', 'text': {'titleSize': 12, 'valueSize': 30}})

    def series(self, title: str, expr: str | list[tuple[str, str]], x: int, y: int,
               unit: str, description: str = '', legend: str = '{{node}}', w: int = 12):
        expressions = [(expr, legend)] if isinstance(expr, str) else expr
        defaults = {'unit': unit, 'decimals': 1, 'noValue': '—',
                    'color': {'mode': 'palette-classic'},
                    'custom': {'drawStyle': 'line', 'lineWidth': 1.5, 'fillOpacity': 7,
                               'spanNulls': False, 'showPoints': 'never', 'axisBorderShow': False}}
        if unit == 'percent':
            defaults.update(min=0, max=100)
        return self.add(title, 'timeseries', x, y, w, 8, description,
                        targets=[target(query, i, name) for i, (query, name) in enumerate(expressions)],
                        fieldConfig={'defaults': defaults, 'overrides': []},
                        options={'legend': {'displayMode': 'table', 'placement': 'bottom', 'calcs': ['last', 'max'],
                                            'showLegend': True},
                                 'tooltip': {'mode': 'multi', 'sort': 'desc'}})

    def table(self, title: str, fields: list[tuple[str, str]], x: int, y: int, w: int, h: int,
              *, key: str, key_labels: str, label_names: dict[str, str], description: str = '',
              styles: list[dict] | None = None):
        targets = []
        key_parts = [part.strip('"') for part in key_labels.split(',')]
        for i, (_, query) in enumerate(fields):
            query = f'label_join({query},"{key}"," / ",{key_labels})'
            # Preserve each descriptive label only in its identity query. All
            # numerical queries carry only the join key, avoiding duplicate
            # node/cluster/id columns after Grafana's outer table join.
            extras = []
            if 'node_uname_info' in query:
                extras = ['nodename', 'release', 'machine']
            elif 'node_os_info' in query:
                extras = ['pretty_name']
            elif 'npu_chip_info_name' in query:
                extras = [label for label in ['name', 'pcie_bus_info', 'vdie_id'] if label in label_names]
            elif 'exporter_version' in query:
                extras = ['exporter_version']
            query = f'max by({key}{"," + ",".join(extras) if extras else ""}) ({query})'
            targets.append(target(query, i, table=True, instant=True))
        # Prometheus instant tables expose one Value #<refId> column per target.
        # Join on explicit identities, not row position, time or a device ID shared by hosts.
        value_fields = {f'Value #{chr(65 + i)}': name for i, (name, _) in enumerate(fields) if name}
        include = list(label_names) + list(value_fields)
        names = {**label_names, **value_fields}
        # Grafana's extractor expects a slash-delimited regular expression.
        pattern = '/^' + r' \/ '.join(f'(?<{part}>.+?)' for part in key_parts) + '$/'
        transforms = [{'id': 'joinByField', 'options': {'byField': key, 'mode': 'outerTabular'}},
                      {'id': 'extractFields', 'options': {'source': key, 'format': 'regexp', 'regExp': pattern}},
                      {'id': 'filterFieldsByName', 'options': {'include': {'names': include}}}]
        if 'id' in label_names:
            transforms.append({'id': 'convertFieldType', 'options': {
                'conversions': [{'targetField': 'id', 'destinationType': 'number'}]}})
        transforms.append({'id': 'organize', 'options': {
            'indexByName': {k: i for i, k in enumerate(include)}, 'renameByName': names}})
        field_styles = list(styles or [])
        for key, name in label_names.items():
            if key in {'nodename', 'pretty_name', 'release', 'name', 'pcie_bus_info', 'vdie_id', 'exporter_version'}:
                # The native inspect drawer exposes/copies the complete value
                # without forcing long metadata to widen every table column.
                field_styles.append(override(name, **{'custom.inspect': True}))
        return self.add(title, 'table', x, y, w, h, description,
                        targets=targets, transformations=transforms,
                        fieldConfig={'defaults': {'noValue': '—', 'decimals': 1,
                                                  'custom': {'align': 'left', 'filterable': True, 'minWidth': 70}},
                                     'overrides': field_styles},
                        options={'showHeader': True, 'cellHeight': 'sm', 'enablePagination': True,
                                 'sortBy': [{'displayName': label_names.get('node', next(iter(label_names.values()))), 'desc': False}]})

    def row(self, title: str, y: int):
        return self.add(title, 'row', 0, y, 24, 1, collapsed=False, panels=[])

    def collapse(self, title: str, y: int, build):
        row = self.row(title, y)
        start = len(self.panels)
        build()
        row['panels'] = self.panels[start:]
        del self.panels[start:]
        row['collapsed'] = True


def percent(name: str, width: int = 85) -> dict:
    return override(name, unit='percent', min=0, max=100,
                    **{'custom.width': width, 'custom.cellOptions': {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'},
                       'color': {'mode': 'fixed', 'fixedColor': 'blue'}})


def status(name: str, good: str, bad: str) -> dict:
    return override(name, decimals=0, **{'custom.width': 75, 'custom.cellOptions': {'type': 'color-text'},
                    'mappings': [{'type': 'value', 'options': {'0': {'text': bad, 'color': 'red'},
                                                             '1': {'text': good, 'color': 'green'}}}]})


def variables(detail: bool) -> list[dict]:
    result = []
    for name, label, query in [
        ('cluster', '环境', 'label_values(up{job=~"node-exporter|npu-exporter"}, cluster)'),
        ('node', '节点', 'label_values(up{job=~"node-exporter|npu-exporter",cluster=~"$cluster"}, node)'),
    ]:
        single = detail
        result.append({'name': name, 'label': label, 'type': 'query', 'datasource': copy.deepcopy(DS),
                       'query': query, 'refresh': 1, 'sort': 1, 'multi': not single,
                       'includeAll': not single, 'allValue': '.*' if not single else None,
                       'current': {} if single else {'text': '全部', 'value': '$__all'}, 'options': []})
    if detail:
        result.append({'name': 'nic', 'label': '宿主网卡', 'type': 'query', 'datasource': copy.deepcopy(DS),
            'query': f'label_values(node_network_receive_bytes_total{{{SCOPE},device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*|virbr.*"}}, device)',
                       'refresh': 1, 'sort': 1, 'multi': True, 'includeAll': False, 'current': {}, 'options': []})
    return result


def base(detail: bool) -> dict:
    return {'uid': DETAIL_UID if detail else UID,
            'title': '节点详情 · 宿主与 NPU' if detail else '集群概览 · 宿主与 NPU',
            'description': 'Node Exporter 与 Ascend NPU Exporter 的运行观测。当前值只使用90秒内成功采样，缺失保持空值；不表示部署预留或可分配资源。',
            'schemaVersion': 39, 'version': 6, 'editable': False, 'refresh': '30s',
            'time': {'from': 'now-1h', 'to': 'now'}, 'timezone': 'browser',
            'tags': ['motor-console', 'node-exporter', 'ascend'],
            'tooltip': {'shared': True}, 'graphTooltip': 1,
            'templating': {'list': variables(detail)},
            'links': [{'title': '集群概览' if detail else '节点详情', 'type': 'link', 'targetBlank': False,
                       'url': '/d/' + (UID if detail else DETAIL_UID), 'includeVars': detail, 'keepTime': True}]}


def summary(p: Panels, detail: bool):
    if detail:
        p.stat('CPU占用', CPU, 0, 'percent', '宿主所有逻辑 CPU 的平均忙碌比例。', 1)
        p.stat('内存占用', MEM, 4, 'percent', '1 − MemAvailable / MemTotal；不是容器内存限制。', 1)
    else:
        all_up = f'({collector("node-exporter")} or {collector("npu-exporter")})'
        p.stat('采集成功', f'sum({all_up} == bool 1)', 0, description='成功抓取的 Node/NPU Exporter 目标数，不是节点数；见下方采集诊断。')
        p.stat('NPU 设备', f'count({IDENTITY})', 4, description='身份有效且来源唯一的已采样芯片设备数，不是空闲卡数。')
    p.stat('NPU利用率', f'avg({UTIL})', 8, 'percent', 'NPU AICore 利用率：仅对当前有效采样设备求平均；完整覆盖见设备表。', 1)
    p.stat('HBM 占用', f'100 * sum({HBM_USED}) / sum({HBM_PAIRED_TOTAL})', 12, 'percent', '已用/容量成对有效后按总容量加权；不代表模型独占。', 1)
    p.stat('最高温度', f'max({TEMP})', 16, 'celsius', '当前有效芯片设备的最高温度；具体设备见节点详情。', 1)
    p.stat('NPU 功耗', f'sum({POWER})', 20, 'watt', '已采样设备的功耗之和；不是整机输入功耗。', 0)


def system_table(p: Panels, y: int):
    return p.table('系统与容量', [('', host('node_uname_info', labels='nodename,release,machine')),
                                ('', host('node_os_info', labels='pretty_name')),
                                ('逻辑 CPU', CORES), ('内存容量', MEM_TOTAL),
                                ('Swap 容量', host('node_memory_SwapTotal_bytes')), ('运行时间', UPTIME)],
                   0, y, 24, 7, key='host_key', key_labels='"cluster","node"',
                   label_names={'node': '节点', 'nodename': '主机名', 'pretty_name': '操作系统', 'release': '内核', 'machine': '架构'},
                   description='操作系统来自 node_os_info.pretty_name；内核和架构来自 uname。缺失字段保持空值。',
                   styles=[override('内存容量', unit='bytes'), override('Swap 容量', unit='bytes'),
                           override('运行时间', unit='s', decimals=0), override('逻辑 CPU', decimals=0)])


def diagnostics(p: Panels, y: int):
    def target_metric(metric: str, job: str):
        sample = selector(metric, job)
        return f'({sample} and {fresh(sample)} and on(cluster,job,instance) {collector(job)})'
    def both(metric: str):
        return f'({target_metric(metric, "node-exporter")} or {target_metric(metric, "npu-exporter")})'
    up = f'({collector("node-exporter")} or {collector("npu-exporter")})'
    age = '(' + ' or '.join(f'(time() - timestamp({selector("up", job)})) and {fresh(selector("up", job))}'
                            for job in ['node-exporter', 'npu-exporter']) + ')'
    versions = []
    for metric, job, label in [('node_exporter_build_info', 'node-exporter', 'version'),
                               ('npu_exporter_version_info', 'npu-exporter', 'exporterVersion')]:
        versions.append(f'label_replace({target_metric(metric, job)},"exporter_version","$1","{label}","(.+)")')
    return p.table('采集目标与版本', [('状态', up), ('距上次抓取', age),
                                   ('抓取耗时', both('scrape_duration_seconds')),
                                   ('样本量', both('scrape_samples_scraped')), ('', '(' + ' or '.join(versions) + ')')],
                   0, y, 24, 9, key='target_key', key_labels='"cluster","node","job","instance"',
                   label_names={'node': '节点', 'job': '采集器', 'instance': '目标地址', 'exporter_version': 'Exporter 版本'},
                   description='年龄是最近抓取时间，并非硬件更新承诺。抓取成功只说明 Exporter 可读，不代表模型健康。每个目标分别列出，重复来源也可见。',
                   styles=[status('状态', '成功', '失败'), override('距上次抓取', unit='s'),
                           override('抓取耗时', unit='s', decimals=3), override('样本量', decimals=0)])


def fleet() -> dict:
    p = Panels()
    summary(p, False)
    node_link = '/d/' + DETAIL_UID + '?var-node=${__data.fields["节点"]}&var-cluster=${__data.fields["环境"]}&from=${__from}&to=${__to}'
    p.table('节点资源一览', [('宿主采集', unique(collector('node-exporter'), HOST)),
                            ('NPU 采集', unique(collector('npu-exporter'), HOST)),
                            ('CPU', CPU), ('内存', MEM), ('NPU 数', f'count by({HOST}) ({IDENTITY})'),
                            ('NPU 均值', f'avg by({HOST}) ({UTIL})'), ('HBM 最高', f'max by({HOST}) ({HBM})'),
                            ('最高温度', f'max by({HOST}) ({TEMP})'),
                            ('已报异常', f'sum by({HOST}) ({HEALTH} == bool 0)'),
                            ('健康采样', f'count by({HOST}) ({HEALTH})')],
            0, 4, 24, 8, key='host_key', key_labels='"cluster","node"', label_names={'cluster': '环境', 'node': '节点'},
            description='点击节点进入逐卡详情。NPU 均值仅包括有效样本，HBM 最高为当前单芯片最高已用比例；— 表示缺失或过期，不补零。',
            styles=[override('环境', **{'custom.width': 75}),
                    override('节点', links=[{'title': '查看节点详情', 'url': node_link}], **{'custom.width': 120}),
                    status('宿主采集', '成功', '失败'), status('NPU 采集', '成功', '失败'),
                    percent('CPU'), percent('内存'), override('NPU 数', decimals=0, **{'custom.width': 60, 'custom.minWidth': 60}),
                    percent('NPU 均值'), percent('HBM 最高'), override('最高温度', unit='celsius', **{'custom.width': 85}),
                    override('已报异常', decimals=0, **{'custom.width': 90, 'custom.cellOptions': {'type': 'color-text'},
                             'color': {'mode': 'thresholds'}, 'thresholds': {'mode': 'absolute', 'steps': [{'value': None, 'color': 'text'}, {'value': 1, 'color': 'red'}]}}),
                    override('健康采样', decimals=0, **{'custom.width': 90})])
    p.series('CPU 使用率 · 节点对比', CPU_HISTORY, 0, 12, 'percent', '固定2分钟窗口，30秒预计算。历史从规则启用后开始积累，不回填此前数据；不改变原始CPU计数器保留期。')
    p.series('内存使用率 · 节点对比', MEM, 12, 12, 'percent')
    p.series('NPU 利用率 · 节点均值', f'avg by({HOST}) ({UTIL})', 0, 20, 'percent', '不把64个设备叠在同一张概览图；逐卡变化请进入节点详情。')
    p.series('HBM 使用率 · 节点峰值', f'max by({HOST}) ({HBM})', 12, 20, 'percent', '各节点中已用 HBM 比例最高的设备；不是设备预留或资源准入。')
    for panel in p.panels:
        if panel['type'] == 'timeseries':
            # Overview values already live in the node table. A compact list
            # keeps all node names visible without a tall duplicate stats table.
            panel['options']['legend'].update(displayMode='list', calcs=[])
    p.collapse('采集诊断与系统信息', 28, lambda: (diagnostics(p, 29), system_table(p, 38)))
    p.collapse('指标说明', 29, lambda: p.add('采样与读数', 'text', 0, 30, 24, 5,
               options={'mode': 'markdown', 'content': '每 **30 秒**刷新。当前值要求目标抓取成功、指标与目标时间戳均在 **90 秒**内；缺失、过期或重复来源保持空值。\n\n**芯片设备数、进程数、利用率均不等于空闲卡数或部署许可。** HBM 按 Exporter MiB 换算；功耗仅覆盖上报芯片。网络只表示选中宿主网卡，不包含 NPU 专用互联。\n\n趋势遵循所选时间范围；历史峰值是历史观测。需要检查单个设备时，点击节点链接进入详情。'}))
    return {**base(False), 'panels': p.panels}


def detail() -> dict:
    p = Panels()
    summary(p, True)
    p.table('NPU 设备明细', [('', IDENTITY), ('健康', HEALTH), ('利用率', UTIL),
                          ('HBM 已用', f'{HBM_USED} / 1024'), ('HBM 容量', f'{HBM_PAIRED_TOTAL} / 1024'),
                          ('HBM 比例', HBM), ('温度', TEMP), ('功耗', POWER), ('上报进程', PROCESSES)],
            0, 4, 24, 17, key='device_key', key_labels='"cluster","node","id"',
            label_names={'id': '设备 ID', 'name': 'Exporter 型号'},
            description='每行一个节点内的芯片设备，按环境/节点/设备身份连接。型号保留 Exporter 原始字符串，不推断销售型号。0 进程不能证明设备可分配。',
            styles=[override('设备 ID', decimals=0, **{'custom.width': 80}), override('Exporter 型号', **{'custom.width': 200}),
                    status('健康', '正常', '异常'), percent('利用率'),
                    override('HBM 已用', unit='gbytes', decimals=1), override('HBM 容量', unit='gbytes', decimals=1),
                    percent('HBM 比例'), override('温度', unit='celsius'), override('功耗', unit='watt'),
                    override('上报进程', decimals=0)])
    p.series('NPU 利用率 · 逐设备', UTIL, 0, 21, 'percent', legend='NPU {{id}}')
    p.series('HBM 使用率 · 逐设备', HBM, 12, 21, 'percent', legend='NPU {{id}}')
    system_table(p, 29)
    def cpu_memory():
        p.series('CPU 使用率', CPU_HISTORY, 0, 37, 'percent', '固定2分钟窗口，30秒预计算。历史从规则启用后开始积累，不回填此前数据。')
        p.series('宿主内存', [('(' + MEM_PAIRED_TOTAL + ' - on(' + HOST + ') ' + MEM_AVAIL + ')', '已用'), (MEM_AVAIL, '可用')], 12, 37, 'bytes')
        p.series('每核负载', [(f'{host("node_load" + n)} / on({HOST}) {CORES}', n + ' 分钟') for n in ['1', '5', '15']], 0, 45, 'none', 'Linux load average / 逻辑 CPU 数；不是 CPU 使用百分比。', w=24)
    p.collapse('CPU、内存与负载', 36, cpu_memory)
    def npu_environment():
        p.series('芯片温度', TEMP, 0, 38, 'celsius', legend='NPU {{id}}')
        p.series('芯片功耗', POWER, 12, 38, 'watt', legend='NPU {{id}}')
        p.series('HBM 温度', HBM_TEMP, 0, 46, 'celsius', legend='NPU {{id}}')
        p.series('HBM 带宽利用率', HBM_BANDWIDTH, 12, 46, 'percent', 'HBM 带宽繁忙比例，不是容量使用比例，也不是 GB/s。', legend='NPU {{id}}')
    p.collapse('NPU 温度、功耗与 HBM 带宽', 37, npu_environment)
    def device_process():
        identity = f'({chip("name", labels="name,pcie_bus_info,vdie_id")} == 1)'
        p.table('NPU 设备身份', [('', identity)], 0, 39, 24, 12,
                key='device_key', key_labels='"cluster","node","id"',
                label_names={'id': '设备 ID', 'name': 'Exporter 型号', 'pcie_bus_info': 'PCI BDF', 'vdie_id': 'VDIE ID'},
                description='保留实际硬件标识；VDIE ID 不称为标准 UUID，不根据型号字符串推断销售规格。',
                styles=[override('设备 ID', decimals=0, **{'custom.width': 80}), override('VDIE ID', **{'custom.width': 440})])
        process = valid('npu_chip_info_process_info', extra='process_id=~"[1-9][0-9]*"')
        process_keys = DEVICE + ',process_id'
        raw = selector('npu_chip_info_process_info', 'npu-exporter', 'process_id=~"[1-9][0-9]*"')
        process_age = unique(f'((time() - timestamp({raw})) and {process})', process_keys)
        p.table('Exporter 上报进程', [('设备内存', unique(process, process_keys)), ('样本年龄', process_age)],
                0, 51, 24, 12, key='process_key', key_labels='"cluster","node","id","process_id"',
                label_names={'id': '设备 ID', 'process_id': '上报 PID'},
                description='每行是 Exporter 上报的设备/PID，不推测进程名、模型名或容器归属。设备内存以 MiB 计；0 进程或缺行均不能证明设备可分配。',
                styles=[override('设备 ID', decimals=0), override('设备内存', unit='mbytes', decimals=1),
                        override('样本年龄', unit='s', decimals=1)])
    p.collapse('设备身份与进程明细', 38, device_process)
    def storage_network():
        filesystem = 'fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|proc|sysfs|cgroup.*|tracefs|debugfs|securityfs",mountpoint!~"/run.*|/var/lib/docker/.*|/var/lib/kubelet/.*"'
        fs_size = unique(valid('node_filesystem_size_bytes', 'node-exporter', extra=filesystem), HOST + ',device,mountpoint')
        fs_error = unique(valid('node_filesystem_device_error', 'node-exporter', extra=filesystem), HOST + ',device,mountpoint')
        fs_avail = f'({unique(valid("node_filesystem_avail_bytes", "node-exporter", extra=filesystem), HOST + ",device,mountpoint")} <= on({HOST},device,mountpoint) ({fs_size} > 0)) and on({HOST},device,mountpoint) ({fs_error} == 0)'
        fs_size = f'({fs_size} and on({HOST},device,mountpoint) ({fs_avail}))'
        p.table('文件系统容量', [('已用比例', f'100 * (1 - ({fs_avail} <= on({HOST},device,mountpoint) {fs_size}) / on({HOST},device,mountpoint) ({fs_size} > 0))'),
                             ('可用空间', fs_avail), ('总容量', fs_size)], 0, 40, 12, 10,
                key='filesystem_key', key_labels='"cluster","node","device","mountpoint"',
                label_names={'mountpoint': '挂载点', 'device': '设备'},
                description='按文件系统列出，不将多个挂载点累加为磁盘总容量；可用空间不等于空闲块数。',
                styles=[percent('已用比例'), override('可用空间', unit='bytes'), override('总容量', unit='bytes')])
        net = 'device=~"$nic"'
        p.series('宿主网络吞吐', [(rate('node_network_receive_bytes_total', extra=net) + ' * 8', '{{device}} · 接收'),
                                (rate('node_network_transmit_bytes_total', extra=net) + ' * 8', '{{device}} · 发送')],
                 12, 40, 'bps', '顶部选择网卡；bond 与从接口分别显示、不相加。不是 NPU HCCS/RoCE 流量。')
        disk = 'device!~"loop.*|ram.*|dm-.*"'
        p.series('磁盘读写吞吐', [(rate('node_disk_read_bytes_total', extra=disk), '{{device}} · 读'),
                                (rate('node_disk_written_bytes_total', extra=disk), '{{device}} · 写')], 0, 50, 'Bps')
        p.series('网卡错误与丢包', [(rate('node_network_' + kind + '_total', extra=net), '{{device}} · ' + title)
                                 for kind, title in [('receive_errs', '接收错误'), ('transmit_errs', '发送错误'),
                                                     ('receive_drop', '接收丢包'), ('transmit_drop', '发送丢包')]],
                 12, 50, 'ops', '计数器变化率；缺失系列不补零。')
    p.collapse('存储与宿主网络', 39, storage_network)
    p.collapse('采集诊断', 40, lambda: diagnostics(p, 41))
    return {**base(True), 'panels': p.panels}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'observability' / 'dashboards')
    parser.add_argument('--rules-output-dir', type=Path,
                        help='Defaults to a rules sibling of the dashboard output directory.')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, dashboard in [('host-npu.json', fleet()), ('host-npu-node.json', detail())]:
        (args.output_dir / filename).write_text(
            json.dumps(dashboard, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    rule_dir = args.rules_output_dir or args.output_dir.parent / 'rules'
    rule_dir.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML, and keeps this offline generator in the standard library.
    rule = {'groups': [{'name': 'qinghuan-host-cpu', 'interval': '30s',
                        'rules': [{'record': CPU_RECORD_NAME, 'expr': CPU.replace(',' + SCOPE, '')}]}]}
    (rule_dir / 'host-npu.rules.yaml').write_text(
        json.dumps(rule, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
