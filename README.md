### Hi, I'm AkihiGod

运维方向，关注发布链路与可观测性。项目均在一台 16G 的 Windows 笔记本上用 Docker 运行，未使用云服务。

#### 运维

**[release-platform](https://github.com/AkihiGod/release-platform)** — 发布与自动回滚平台

一条命令完成：构建镜像 → 起新版本 → 健康检查 → 切流量 → 观察 15 秒。

- 健康检查不通过则不切流量，删除新容器，线上不受影响
- 切流量后才发现故障，自动切回旧版本并验证
- 旧容器保留不删除，回滚只需一次 nginx reload

蓝绿发布，使用 nginx + Docker，未使用 K8s。

**[monitor-loop](https://github.com/AkihiGod/monitor-loop)** — 监控告警闭环

除标准的 Prometheus 与 Grafana 外，另写了两个组件：

- `exporter/` — Exporter：探测进程存活、端口连通、磁盘
- `alert-hub/` — 告警处理服务：接收 Alertmanager webhook，实现去重合并、超时升级、恢复闭环，数据存 SQLite

#### 测试开发

- [接口自动化测试](https://gitee.com/akihiGod/akihi-god/tree/api-testing-demo)：pytest + requests，含请求封装、数据驱动、参数化用例、异常与边界场景、HTML 报告
- [RAG 测试问答与用例生成](https://gitee.com/akihiGod/akihi-god/tree/testpilot)（毕业论文方向）
- [规则与 LLM 变异对比及补测闭环](https://gitee.com/akihiGod/akihi-god/tree/mutation-research)
