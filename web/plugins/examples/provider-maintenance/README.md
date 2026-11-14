# 独立概览提供者

这是 Python Provider SDK 的完整源码示例，不是模型或监控数据模拟器。选择后仅替换 `api.overview`：返回明确的 HTTP 503 维护提示，证明默认概览可以被独立提供者替换；没有选择它时不会运行其入口。其他接口仍经过宿主原有的认证、角色、CSRF、审计和归属检查。

本例不适合一直作为生产概览使用，也不停止模型、不修改原生 Motor。插件代码在受信进程中执行，不是沙箱。

在仓库根目录，使用安装了锁定依赖的 Python：

```sh
python web/scripts/provider_packages.py build \
  web/plugins/examples/provider-maintenance /secure/maintenance.mcprovider
python web/scripts/provider_packages.py inspect /secure/maintenance.mcprovider
python web/scripts/provider_packages.py import /secure/maintenance.mcprovider \
  --directory /opt/motor-console/provider-artifacts
```

输出描述符中的 `trust_code` 默认为 false。审查来源、代码、许可证与摘要后，将描述符加入独立装配文件的 `providers`，显式改为 true，并加入绑定：

```json
{
  "schemaVersion": 1,
  "providers": [],
  "bindings": {
    "api.overview": {
      "provider": "community.maintenance-overview",
      "version": "1.0.0",
      "contract_version": 1
    }
  }
}
```

上面 `providers` 留空以避免伪造来源摘要；必须填入真实导入输出。`module_file` 要从运行 API 的文件系统可读。容器部署应只读挂载同一制品，并按容器内路径生成/审查描述符，不能直接使用本机路径。

运行 `provider_packages.py validate` 校验完整候选装配文件。`select` 使用当前文件 SHA256 做比较并写独立备份，只提交下次启动的选择；在维护窗口设置 `MOTOR_PROVIDER_ASSEMBLY_FILE` 指向该文件并重启 API。关闭/回退选择不重启模型。确认概览显示维护失败、其他授权接口仍可用；随后选择原绑定并重启 API 恢复默认概览。

这是受信本地提供者装配格式，与增量社区 `.qhplugin`、UI `.mcui` 及 Node 宿主模块分别维护版本，不能互换包格式。完整合同与恢复流程见[提供者部署](../../../docs/PROVIDER_PLUGINS.md)。
