# 界面设计系统与来源

平台采用面向运维的云控制台布局：固定导航、清晰的资源列表、分组详情与受控操作。布局和通用控件主要参考 **Portainer Community Edition 2.45.3**，对应源码提交 `0fa764dc81c76bb7914d65d8e62b026d7c746899`；中文、模型/NPU 字段、权限与执行反馈按平台业务组织。平台是独立项目，不代表 Portainer 或 MindIE-Motor 官方产品。

## 参照范围

| 参照 | 平台中的用途 |
| --- | --- |
| [登录模板](https://github.com/portainer/portainer/blob/0fa764dc81c76bb7914d65d8e62b026d7c746899/app/portainer/views/auth/auth.html) | 居中账号表单、明确的主操作；平台另有首次初始化与 OIDC |
| [Home / Environments](https://docs.portainer.io/user/home) | 状态筛选、排序、搜索和 Agent 卡片；卡片操作对应平台已有 API |
| [Containers](https://docs.portainer.io/user/docker/containers) | 推理服务表格、状态与行操作 |
| [Container details](https://docs.portainer.io/user/docker/containers/view) | 服务及 Agent 详情分组、配置、日志与危险操作 |
| [Dashboard](https://github.com/portainer/portainer/blob/0fa764dc81c76bb7914d65d8e62b026d7c746899/app/react/docker/DashboardView/DashboardView.tsx) | 概况信息、资源观测与操作记录 |

主题参考 [theme.css](https://github.com/portainer/portainer/blob/0fa764dc81c76bb7914d65d8e62b026d7c746899/app/assets/css/theme.css)，按钮和表格分别参考 [button.css](https://github.com/portainer/portainer/blob/0fa764dc81c76bb7914d65d8e62b026d7c746899/app/assets/css/button.css) 与 [datatable.css](https://github.com/portainer/portainer/blob/0fa764dc81c76bb7914d65d8e62b026d7c746899/app/react/components/datatables/datatable.css)。固定源码链接用于追溯设计来源；外部产品的当前文档可能已与此版本不同。

## 布局与控件约定

- 展开侧栏默认 240px，可在系统设置按 8px 步长调整为 200–280px；折叠宽度 72px。顶栏高 67px；页面标题为 18px、28px 行高、500 字重。
- 使用客户端系统字体，不分发字体二进制；图标使用 Lucide，依赖版本由前端锁文件固定。避免在同一层级混用字体、图标线宽和强调色。
- 深色工作区以 `#121212`、面板以 `#212121` 为基础；侧栏为石墨灰。颜色由共享主题变量控制，新页面应复用状态色。
- 主按钮采用深浅中性色反转，链接、键盘焦点与选中态使用强调色。保留蓝灰、红灰、青灰配色，以及浅色、深色、跟随系统和界面密度偏好。
- 按钮高度为 30/34/46px，圆角 8px；输入框高度 34px，圆角 5px。确认窗口基准宽 450px，表单窗口提供 700/1000px 尺寸；小视口受可用宽度限制。
- 详情抽屉桌面贴右、全高、50vw、最大宽 672px，移动端全宽。详情应出现在当前可见的窗口或抽屉中，不能让用户滚到列表末尾寻找。
- 节点状态条高 74px，节点卡片行约 104px，右侧操作栏宽 44px；普通表格行以 50px 为基准，多行内容自然增高，不以固定行高截断错误或说明。

页面使用可配置的产品名称、副标题和图标。Docker/Kubernetes 标识只用于辨识运行环境；其商标归相应权利人所有。第三方产品商标不作为平台品牌，外部参考页面截图不作为平台资产。

## 交互与数据表达

每个按钮应连接真实操作、明确的禁用原因或可阅读的说明。只读用户的可见性与后端权限同时生效；危险操作保留确认、目标识别与执行中保护。只显示由确定来源获得的数据，缺失数据保持缺测语义，不补零，也不用“在线”代替资源新鲜度或模型就绪。

保留短暂、可中断的悬停、焦点、窗口和抽屉反馈。动效不能延迟操作、遮挡数据或持续触发布局变化；遵循系统减少动态效果偏好。键盘用户应能完成导航、打开和关闭窗口，嵌套窗口应隔离焦点，关闭后返回触发控件。状态必须同时有文字或图标，不能只用颜色区分。

新增页面应复用共享主题和控件，核对浅/深色、三套配色、舒适/紧凑密度、展开/折叠侧栏、窄视口、长中文/英文文本、错误、空状态、加载与只读权限。表格说明应换行或进入详情，不得覆盖相邻列；弹窗应保留可见关闭入口和可滚动内容。刷新不得重置用户输入、选项或滚动位置，也不能叠加未完成请求。

实现入口为 [design-tokens.css](../frontend/src/design-tokens.css)、[主题偏好](../frontend/src/appearance.tsx) 与 [界面组件目录](../frontend/src)。新增样式前应先定位同类共享控件及其主题变量；具体模块路径以源码树为准。

## 来源与许可证

Portainer 的来源及许可证见根目录 [NOTICE](../../NOTICE.md)；Lucide 的相关通知随依赖保留。设计参照不代表业务逻辑复用或功能等同。浏览器视觉检查、源码构建、容器构建和真实 Agent/模型验收属于不同验证范围，应分别记录实际结果。
