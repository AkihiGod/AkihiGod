### Hi, I'm AkihiGod 👋

运维方向，主要折腾两件事：**发布链路**和**可观测性**——说白了就是"上线怎么不出事，出了事怎么发现、怎么退回来"。

手上没有云，所有项目都在一台 16G 的 Windows 笔记本上用 Docker 跑通：单机复现出集群才有的那套流程，该省的省，该自己写的自己写。

#### 运维

**[release-platform](https://github.com/AkihiGod/release-platform)** — 发布与自动回滚平台

一条命令走完：构建镜像 → 起新版本 → 健康检查 → 切流量 → 观察 15 秒。

- 健康检查没过就**不切流量**，把新容器删掉，线上毫无感觉
- 切过去之后才发现坏（"起来几秒才开始报错"那种），**自动切回旧版本**并验证切回成功
- 旧容器一直留着不删，所以回滚只是一次 nginx reload

蓝绿发布，nginx + Docker，没上 K8s。

**[monitor-loop](https://github.com/AkihiGod/monitor-loop)** — 监控告警闭环

重点不在"装了 Prometheus 和 Grafana"，而在两个自己写的组件：

- `exporter/` — 自研 Exporter：探进程存活、端口连通、磁盘
- `alert-hub/` — 自研告警处理服务：接 Alertmanager 的 webhook，做去重合并、超时升级、恢复闭环，全部落 SQLite

#### 测试开发

写代码这块的主线是**测试**，几个仓私有：

- RAG 测试问答 + 用例生成（毕业论文方向）
- 一个约束 agent 写代码的质量规范仓：三层测试 / 覆盖率门禁 / 变异测试 / CI
- 规则与 LLM 变异对比 + 补测闭环的研究仓
