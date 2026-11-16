# 后端来源

Python 后端迁自本机 fuckZHS 项目的工作目录快照（2026-11-09），其 Git HEAD 为
415bd88786ec783b79c96b9e7095011f91c8de0f。原 Python 项目的 MIT License 保留在 LICENSE。

课堂派 ktpwarp-server 的来源、上游版本和修改说明见 integrations/ketangpai/NOTICE.md，
该目录的原始 LICENSE 及 README 同时保留。

此次迁入保留平台协议实现和核心回归测试，去掉旧 WPF/PySide6 UI、审计脚本、备份、个人凭据和历史运行数据。
本地改动集中在账号数据目录隔离，以及智慧树设置即时生效；C# 桌面通过 JSONL 复用这些适配器。
