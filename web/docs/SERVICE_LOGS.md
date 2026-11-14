# 推理服务日志：来源、筛选与近期窗口

服务详情中的“运行日志”读取原生容器标准输出，不修改模型进程或原生日志配置。Docker 和 Kubernetes 使用相同的展示字段；平台自己的系统日志、操作审计和轮转设置见 [部署手册](../DEPLOYMENT.md)。

读取要求服务对应的运行时插件可用，并且执行 Agent 仍拥有相应资源；关闭页面或停用运行时不等于删除原生日志。

## 来源与时间

- Docker 行保留容器 ID、名称、原生角色和注册节点；Kubernetes 行保留命名空间、Pod、容器及实际工作节点。`agent_node_id` 表示接入的执行 Agent，K8s 场景下不等于运行 Pod 的工作节点。
- `time` 来自 Docker/kubectl 提供的带时区时间戳，保留原始精度。无法识别的时间不替换为“现在”，不自行给无时区文本套时区；`timestamp_valid=false` 时界面不展示猜测时间。
- `observed_at` 只可能是平台诊断的观察时刻，不能当成业务日志产生时间。
- `message` 去除 ANSI 控制序列用于阅读；`raw_message` / `raw_line` 保留来源文本供核对，仍执行凭据脱敏。它们不是可恢复秘密的原始归档。

## 级别

只接受可确认的 JSON `level` / `levelname` 或标准日志前缀。`WARNING` 归一为 `WARN`、`CRITICAL` 归一为 `FATAL`；正文中仅出现“warning/error”不会据此判级。无法确认时为 `UNKNOWN`，不自动标成 `INFO`；界面可以保持中性原始输出样式。

组合管理容器的真实角色仍为 `coordinator_controller`。筛选 `coordinator` 或 `controller` 会包含该组合容器，但不会声称已把其中两类无结构输出精确拆开；`management` 包含确定的管理角色。`single_container` 保持整体来源，不猜测某一行属于 Prefill 还是 Decode。

## 筛选和分页

已登录用户可读取：

```text
GET /api/services/<service-id>/logs?tail=200
GET /api/services/<service-id>/logs?node_id=<registered-node-id>&role=prefill&tail=200
GET /api/services/<service-id>/logs?tail=200&limit=50&offset=50
```

节点必须属于该服务运行布局。角色在 Agent 读取、截尾之前筛选；支持 `prefill`、`decode`、`encode`、`union`、`coordinator`、`controller`、`coordinator_controller`、`management`、`single_container`、`kv_store`、`kv_conductor`、`render`。不接受任意命令或未定义角色。

默认采用按节点、Pod/容器和角色分源的公平配额，再按可识别的原生时间稳定排序。一个持续大量输出的 Decode 不会把管理与 Prefill 来源全部挤出窗口。请求条数小于来源数量时，窗口无法同时覆盖所有来源，需按来源筛选。

这是**最多2000条的近期有界窗口**，不是全量导出或持久归档。`limit` / `offset` 仅对本次窗口分页；新日志产生后重新请求的窗口可能变化，因此响应明确 `consistent_snapshot=false`。要核对完整日志，应使用运维侧既有的 Docker/K8s 日志保留或独立日志平台。

响应保留 `items`，并增加：

| 字段 | 含义 |
| --- | --- |
| `partial` / `errors` | 是否有来源不可读取、超时或截断；有部分行不代表所有来源成功 |
| `limit` / `offset` / `total` / `has_more` | 当前有界窗口的分页信息，`total` 不是容器历史日志总数 |
| `sources_total` / `sources_included` | 已取得记录的来源数与当前页覆盖数，不推断未读取的历史来源 |
| `filters` | 本次节点、角色筛选条件 |
| `window` | 固定为 `bounded_recent_tail` |

`errors` 为 `{node_id, node_name, code}` 对象数组。平台诊断也可作为 `source=agent_error` 的明确诊断行，级别由平台确定，时间不伪装成原生业务日志时间。

## 读取成本与不完整结果

平台按需获取日志，不持续跟随所有容器、也不扫描完整历史文件。控制面使用共享的有界读取并发和总时间预算；Agent 对每个来源限制返回字节数，Docker 读取后关闭流，K8s 每次命令受总操作期限约束。发生超时或超出窗口时显示不完整状态，不拿缓存或其他节点日志补作成功。

这些读取限制不设置原生容器的日志轮转、保留天数或集中归档策略，也不改变原生 Motor 逻辑。正式部署仍需由运维侧根据磁盘容量配置 Docker/Kubernetes 日志保留和集中采集。
