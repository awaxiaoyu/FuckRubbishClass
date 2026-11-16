# 后端迁入与运行

无需换语言。C# 负责 WPF、MVVM、账号、课程缓存与任务调度；Python 保留智慧树/知到和学习通协议实现；
TypeScript/Node 保留课堂派签到服务。全部后端源码位于本仓库 backend/，运行不依赖原 fuckZHS 仓库。

## 当前接入

| 平台 | 已接入桌面入口 | 实现 |
| --- | --- | --- |
| 智慧树/知到 | 二维码登录、课程列表、视频、平时测试选择与作答、答案预览与题库采集、知到 AI 课程 | Python Adapter + 原有 engine |
| 学习通 | 独立浏览器登录、课程列表、原生视频、考试列表与选定考试作答、当前题面只读解析 | Python + Playwright |
| 课堂派 | 账号验证、课程目录、服务状态与依赖安装、时间表编辑、启动/停止考勤守护、查看日志 | Python Adapter + Node sidecar |

学习工作台提供视频、平时测试、考试、考勤与 AI 课程五个独立面板，按平台筛选账号、按账号筛选课程；
平时测试仅显示普通课程，AI 面板仅显示 AI 课程，也支持手动填写 Course ID / Class ID。
列表读取支持取消，任务下发后可跳转任务监控查看进度或停止。账号中心支持编辑账号，课程中心的选中数量实时更新。
学习通章节作业未接入。模型中心目前将 FastSolver 传给智慧树答题，
DeepReasoning 传给学习通题面解析，DocumentParser 传给知到课件处理；跨模型自动 fallback 和 Vision 独立路由尚未接通后端。

账号/课程/任务入口不再生成模拟成功、假课程和假进度。课程 metadata（包括 native_id、class_id、CPI）随缓存保存，
任务完成只表示后端流程结束，课程达标仍以平台记录为准。

## 环境与启动

需要 Windows、.NET 8+ SDK、Python 3.10+；课堂派另需 Node.js 18+、pnpm。
安装脚本默认访问 PyPI 与 npm registry；-Offline 仅使用本地可用依赖。

```powershell
.\scripts\Setup-Backend.ps1 -Python 'D:\Python\python.exe'
dotnet run --project .\src\FuckRubbishClass\FuckRubbishClass.csproj
```

传入本机已有 Python 的实际路径即可，不要求它位于 D:\Python。脚本在 backend/.venv 创建本项目的独立环境。
只使用智慧树与学习通可加 -SkipKetangpai。默认浏览器为本机 Edge；选择 Chromium 前需运行：

```powershell
.\backend\.venv\Scripts\python.exe -m playwright install chromium
```

运行时优先使用 FRC_PYTHON（兼容 COURSE_HUB_PYTHON），否则使用 backend/.venv/Scripts/python.exe、
backend/runtime/python/python.exe，最后尝试 PATH 的 python。FRC_BACKEND_ROOT 可显式指定 backend 目录。
开发时会查找当前仓库的 .venv；发布时后端源码随输出复制，Python 环境和 node_modules 不进入 Git 或普通发布包。

发布后可在目标机器运行安装脚本并指定 -BackendRoot '<发布目录>\backend'，或用上述环境变量指定已有运行时。

## 登录与配置

1. 添加账号后，点击账号卡片的“登录”。智慧树显示真实二维码，学习通在托管浏览器中登录；课堂派使用所填账号密码验证。
2. 登录确认后同步课程；“刷新会话”会实际读取课程服务，不会把打开网页视为登录成功。
3. 在模型中心配置模型及角色，在功能页或课程中心下发任务；任务监控接收实际进度与错误。
4. 课堂派先填写账号密码与实际课程时间表，再启动守护；服务日志保存在对应账号的数据目录。

UI 设置保存在 data/unified_config.json，账号在 data/accounts.json。
每个账号的后端设置、cookies.json、.sessions/chaoxing.json、日志和缓存隔离在
data/backend/<账号 ID 的 SHA-256>/。账号配置与 API Key 当前以本地 JSON 保存，尚未实现加密存储。
旧仓库个人 Cookie、配置、密码和答案数据没有自动导入，需要重新登录并设置模型。

智慧树作业默认保留草稿；只有开启 AutoSubmit 且关闭 SubmitSafeguard 才请求提交。
后端若再次要求确认纯 AI 答案，当前客户端默认拒绝该提交并保留草稿。
题面解析会把当前题目发送到配置的模型 API，不会填答或交卷。

## 进程与取消

每个账号一个 Python 进程，同账号串行、不同账号可并行，Playwright 始终在 Python 主线程使用。
C# 通过 JSONL stdin/stdout 通信，先验证 platforms 握手，再处理 stream: progress/event 及最终 ok 响应。
单账号操作锁覆盖配置同步和实际操作，避免并行请求覆盖设置。

取消时先发 cancel 并读完当前响应；5 秒仍未响应则终止该账号进程树，下次请求会重新连接。
关闭窗口时停止任务并释放 Python/Node 进程。课堂派每账号分配一个稳定端口（20000–49999），
可从账号后端 config.json 查看 WebSocket 端口与 path；端口冲突时服务日志会报告启动失败。

## 验证

```powershell
dotnet build .\src\FuckRubbishClass\FuckRubbishClass.csproj
Push-Location .\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
Pop-Location
dotnet run --project .\tests\BridgeSmoke\BridgeSmoke.csproj -- .\backend .\backend\.venv\Scripts\python.exe
dotnet run --project .\tests\UiSmoke\UiSmoke.csproj -- .\artifacts\ui-preview
Push-Location .\backend\integrations\ketangpai
pnpm exec tsc --noEmit
Pop-Location
node --test .\backend\tests\ketangpai-attendance.test.cjs
```

BridgeSmoke 覆盖协议启动、进度、配置映射、账号隔离、metadata 持久化、错误传播、取消后响应清理、无响应强制停止和重连。
UiSmoke 在实际 WPF 控件上检查启动与导航、五个面板切换、账号/课程过滤、作业与考试选择、草稿保护、答案预览、
服务控制、时间表校验、账号编辑、课程勾选通知、错误恢复与 binding errors，并将使用本地测试数据的页面渲染到 artifacts/ui-preview。
其余命令覆盖原后端核心回归。测试不使用个人会话，也不调用真实学习平台；真实平台端到端流程需登录后验证。
