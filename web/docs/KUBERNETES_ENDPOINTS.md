# Kubernetes 服务访问入口

本手册适用于 Kubernetes 服务；Docker 端口仍由对应原生部署布局决定。平台通过只读接口 `GET /api/services/{id}/endpoints` 发现实际已发布的入口。需要登录；接口不接受用户指定 URL、路径或查询参数，也不会修改原生 Motor JSON、Service 或平台数据库。

## 地址从哪里来

Agent 首先核对自己的 Namespace UID 和已记录工作负载 UID，再读取该命名空间的 Service。只有沿唯一的 controller OwnerReference 链能回到本服务受管工作负载时，才使用其端口。名称相似、标签相同或复制了旧 Service 名称，都不足以证明归属。对象重建、UID 不符、权限不足或采集超限时不猜地址。

- **NodePort**：使用真实 `nodePort`，并将集群验证过的 Node InternalIP 与控制台登记地址取交集。`externalTrafficPolicy: Local` 还要求该节点存在已验证、就绪的实际后端。
- **LoadBalancer**：仅展示经过格式校验的实际 ingress 地址。控制台不会解析或请求它，不将其当成已经验证的外部连接。
- **ClusterIP**：属于集群内部访问，不能把 Pod 端口拼到 Agent 宿主 IP 冒充集群外地址。

端口用途依据已部署的原生 Coordinator 配置和实际 Service 端口确认。不能确认的命名 targetPort 不猜测映射。原生配置中的内部端口保持原样，单独的连接信息才使用对外端口。

结果包含来源、采集时间和到期时间，观察窗口最多 15 秒。同一身份的并发读取合并；服务修订、执行连接或登记地址变化后重新核对。读取失败和过期状态不会继续把旧地址展示为新鲜结果。服务尚未部署或已经停止时不发布可用入口。

`ready` 表示已经核对 Service 发布配置、归属、后端及节点候选，**不代表客户端网络、防火墙或认证已经验证**。完成部署后仍应从实际客户端使用该地址发起普通、流式和所需认证请求。平台内置推理调试通过执行 Agent 访问已验证的 Coordinator Pod，不以展示用的 LoadBalancer URL 作为服务端出网目标。

## 需要哪些只读权限

以下仅为入口发现的补充 Role 示例，需替换 `motor-console-example` 并由集群管理员绑定给正确执行身份。它不是完整部署权限，也不会自动授予 ClusterRole、Namespace 创建或工作负载修改能力。

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: motor-endpoint-reader
  namespace: motor-console-example
rules:
  - apiGroups: [""]
    resources: ["services", "pods", "endpoints"]
    verbs: ["get", "list"]
  - apiGroups: ["discovery.k8s.io"]
    resources: ["endpointslices"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets", "statefulsets"]
    verbs: ["get"]
  - apiGroups: ["mindcluster.huawei.com"]
    resources: ["inferservicesets", "inferservices", "instancesets"]
    verbs: ["get"]
```

此外，执行身份需要读取本服务 Namespace 的元数据，以及用于验证地址的集群 Node 元数据。原生部署本身所需的权限应独立审阅。这里不需要读取 Secret、执行容器命令、访问任意命名空间、授予 `bind` / `escalate`，或改变节点调度状态。缺少链路权限会明确返回未解析或不可用，不能退化成按标签猜测。

## 核验与排错

1. 确认原生工作负载和目标 P/D 数量实际就绪，再查看访问入口。
2. 对照真实 Service 的 type、port、targetPort、nodePort 与实际 OwnerReference UID；不要修改数据库中的旧 endpoint 值来代替发现。
3. 若为 Local 策略，核对 EndpointSlice/Endpoints 的就绪后端所在节点。没有就绪后端的节点地址不应被发布。
4. 从实际客户端网络测试真实地址，至少完成一条普通请求及一条流式请求；失败时区分 Service 尚未发布、权限不足、Pod 未就绪、网络不可达及认证拒绝。不要以 Agent 自身能访问 Pod 证明集群外客户端可达。
5. 停止、删除、重建或重新授权后刷新，再核对 UID 与时效。近期观察不是不可变的配置承诺。
