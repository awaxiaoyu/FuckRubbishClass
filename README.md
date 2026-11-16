# FuckRubbishClass

已迁入智慧树/知到、学习通和课堂派后端，采用 C# WPF + Python Adapter + TypeScript/Node sidecar。
安装运行、当前功能范围及验证方式见 [后端接入说明](BACKEND.md)。下文包含界面与架构设计目标，实际接入范围以上述说明为准。

> 现代化统一在线课程自动化与辅助学习工作台（Fluent Design Edition）

[![.NET 8.0](https://img.shields.io/badge/.NET-8.0--windows-512BD4?logo=dotnet)](https://dotnet.microsoft.com/)
[![Fluent Design](https://img.shields.io/badge/UI-Fluent%20Design%20(WPF--UI)-0078D4?logo=windows)](https://wpfui.lepo.co/)
[![MVVM](https://img.shields.io/badge/Architecture-CommunityToolkit.Mvvm-blue)](https://learn.microsoft.com/dotnet/communitytoolkit/mvvm/)

---

## 📖 项目背景与重构愿景

在原桌面端（CourseHub.Desktop）中，各在线平台（智慧树、学习通、课堂派）的功能实现虽然被接入到了一个窗口中，但属于典型的**“强行拼凑、各自为政”**：
- **模型配置割裂**：智慧树有自己的 AI 配置，学习通又有独立的只读解释与自动答题两份独立 API 配置，课堂派与知到 AI 课无法共享模型，缺乏统一的模型池与调度策略；
- **缺乏多账号体系**：各平台会话与凭据存储散乱，无法方便地查看所有账号列表、切换主账号、查看会话有效状态；
- **功能界面碎裂**：通过在单一窗口中硬编码判断平台字符串、频繁切换显示/隐藏零碎控件，缺乏统一的业务抽象；
- **UI 风格混乱**：缺乏标准化的 Fluent 视觉层级与模块化 MVVM 架构。

**FuckRubbishClass** 是对原桌面端前端界面的**彻底重构与升级**。我们采用 **Windows 11 Fluent Design System（Mica 云母材质、现代卡片、微阴影、精致层级）** 与 **CommunityToolkit.Mvvm** 架构，将杂乱的平台孤岛抽象为标准化的核心业务中台：
1. **统一的模型配置与智能调度中心（Model & Scheduler Center）**
2. **多平台账号列表与会话管理中心（Account Management Center）**
3. **跨平台统一功能接入工作台（Unified Feature Workspace）**
4. **统一课程库与批量任务下发（Course & Task Hub）**
5. **实时任务流水线与终端控制台监控（Task & Log Terminal Monitor）**
6. **全局统一日志系统与异步持久化落盘（Global Logging System）**
7. **全局弱引用统一事件总线（Global Unified Event Bus）**

---

## 🌟 核心特性与架构设计

### 1. 全局统一日志系统（Global Logging System）
- **多 Sink 协同架构**：
  - **内存响应式 UI Sink**：线程安全派发到 WPF UI 队列（`ObservableCollection<LogEntry>`），支持日志级别彩色标签与自动容量滚动截断（防止长时间运行内存膨胀）；
  - **异步高性能文件持久化 Sink**：独立的后台无锁缓冲队列与写入线程，自动按日轮转生成磁盘文件（`logs/app-yyyy-MM-dd.log`），进程退出时自动清空缓冲区，绝不阻塞 UI 渲染与网络主流程；
  - **诊断跟踪 Sink**：自动同步输出至 `System.Diagnostics.Trace` 与 `Debug` 窗口。
- **全方位静态与 DI 双支持**：提供全局静态入口 `AppLogger.Info(...)`、`AppLogger.Warn(...)`、`AppLogger.Error(ex, ...)`，支持在任意 ViewModel、服务、静态工具类或转换器中单行调用；
- **终端控制台交互**：提供日志分类过滤、一键复制到剪贴板、清空以及导出全量日志文件功能。

### 2. 全局统一事件总线系统（Global Unified Event Bus）
- **强类型领域事件解耦**：
  - `AccountEvents`：`AccountLoggedInEvent`、`AccountStateChangedEvent`、`PrimaryAccountChangedEvent`、`AccountDeletedEvent`；
  - `TaskEvents`：`TaskJobStartedEvent`、`TaskJobProgressEvent`、`TaskJobCompletedEvent`、`TaskJobCancelledEvent`；
  - `ModelSchedulerEvents`：`ModelInvocationStartedEvent`、`ModelInvocationCompletedEvent`、`ModelFallbackTriggeredEvent`、`ModelHealthTestedEvent`；
  - `SystemEvents`：`NavigationRequestedEvent`（跨模块解耦导航）、`NotificationEvent`（全局气泡消息提示）、`ConfigSavedEvent`。
- **弱引用安全订阅**：基于 `CommunityToolkit.Mvvm` 的弱引用消息机制封装，订阅者生命周期随控件销毁自然释放，彻底避免传统 .NET 事件强引用导致的内存泄漏；
- **自动 UI Dispatcher 调度**：后台工作线程发布的事件在进入订阅者回调前，由总线统一安全切回 UI 主线程执行，无需各 ViewModel 反复手工编写 `Dispatcher.Invoke`。

### 3. 统一模型配置与智能调度中心（AI Engine & Dispatcher）
彻底废除此前在各功能页面中分散填写的 API Key 和 Base URL，建立全局统一的 AI 模型池：
- **主流厂商预设一键加载**：内置 DeepSeek（V3 / R1）、OpenAI（GPT-4o）、Moonshot（Kimi 课件解析）、硅基流动（SiliconFlow）、本地 Ollama 等多种预设；
- **全网连通性与延迟测速（Ping）**：支持一键对所有模型节点发起真实 HTTP 请求，毫秒级呈现节点可用性与响应延迟；
- **基于角色的智能调度与降级容灾（Role Routing & Fallback）**：
  - ⚡ **快速做题角色（Fast Solver）**：专供单选/多选/判断等高频客观题，低延迟高并发（默认 DeepSeek-V3）；
  - 👁️ **视觉识图角色（Vision OCR）**：专供公式截图、题图、课件字形分析（默认 GPT-4o / Qwen-VL）；
  - 🧠 **深度推理角色（Deep Reasoning）**：专供主观问答、期末大题、复杂逻辑辨析（默认 DeepSeek-R1）；
  - 📄 **课件文档解析角色（Document Parser）**：专供知到 AI 智能课 PPT 大纲语义提取（默认 Moonshot Files API）；
- **容灾降级机制**：主模型遇到 429 限流、5xx 服务端错误或超时时，调度器自动无缝切换备用降级节点；遇到图片题自动升阶至视觉节点。

### 2. 统一多平台账号中心（Account Management Center）
- **多平台聚合管理**：智慧树/知到、学习通/超星、课堂派账号统一在同一卡片列表中管理；
- **账号卡片状态仪表盘**：直观展示所属平台色标、院校/机构、绑定课程数、会话有效性指示灯（绿色正常、黄色需刷新、红色离线）；
- **无缝切换主账号**：点击即可切换各平台的首选活跃账号，下发任务无需重复登录；
- **扫码授权交互抽屉**：统一的二维码弹窗与会话刷新轮询，操作流程顺畅。

### 3. 统一功能接入工作台（Unified Feature Workspace）
摆脱传统的“按平台切标签”模式，转为**“按业务功能分类”**的统一接入：
- 📺 **视频自学工作台（Video Study）**：
  - 统一适配智慧树与学习通课程；
  - 统一支持播放倍速滑动调节（0.5x ~ 2.0x）、完成进度阈值（80% ~ 100%）、静音播放、自动跳集、无进展防卡顿检测；
  - 支持 Microsoft Edge、Google Chrome 与无头后台浏览器环境选择。
- 📝 **平时测验与章节作业（Homework & Quiz）**：
  - **分层高效解答管线**：本地已验证题库 -> 答案图高精度识别匹配 -> 统一模型池（FastSolver / Vision）保底；
  - **自动重做支持**：已交卷未满分时，自动申请重做刷新历史分（智慧树支持最多 3 次）；
  - **交卷安全保护机制**：作答结果逐题保存为草稿，未勾选确认前绝不主动交卷，杜绝误交卷风险；
  - 可配置单题作答间隔（防风控检测）。
- 🎯 **考试中心与 AI 智能副驾（Exam Center）**：
  - **100% 安全只读 AI 解析模式**：一键提取当前页面考题，调度深度推理模型生成完整的推导过程与答题建议，纯本地展示，不对考试页面做任何修改与提交，零封号风险；
  - **自动考试模式**：考试列表获取、人机滑块接管等待（30~600秒）、交卷控制。
- ⏱️ **课堂派考勤签到守护（Attendance Sign-In）**：
  - 本地常驻守护进程一键启停；
  - 课程监听时间表配置（按星期与时间段定时监测）；
  - GPS 虚拟坐标精准定位（支持集美大学等高校常用坐标预设）；
  - 自动识别数字签到、GPS 签到、二维码转发与签入签出。
- 🤖 **知到 AI 智能课程助手（AI Smart Course）**：
  - 智能识别 AI 课程 ID 与班级 ID；
  - 课件 PPT 结构化知识提取与过关测验。

### 4. 课程中心与批量任务分发（Course & Task Hub）
- 跨平台聚合所有账号名下的课程；
- 支持按平台筛选（全部 / 智慧树 / 学习通 / 课堂派）与关键词模糊搜索；
- 丰富的卡片展现：课程进度条、未作答测验红点提示、期末考试倒计时；
- 支持多选课程，一键批量下发自学或测验任务。

### 5. 任务调度与实时日志终端（Task & Log Terminal Monitor）
- 正在运行任务的动态横幅：当前步骤描述、百分比仪表盘、已用时长、随时手动终止；
- 任务队列与历史记录面板；
- **现代化暗色控制台终端（Terminal Console）**：
  - 仿类 Unix 终端质感，等宽字体（Consolas）渲染；
  - 日志级别彩色标记：`[INFO]`（蓝）、`[SUCCESS]`（绿）、`[WARN]`（黄）、`[ERROR]`（红）；
  - 支持日志级别过滤（信息/成功/警告/错误）、一键复制日志、清空控制台。

### 6. 多平台多账号多线程并发刷课引擎（Concurrent Multi-Account Multi-Platform Engine）
- **真正的跨平台/跨账号并行能力**：在课程库中一次性勾选来自不同平台（智慧树、学习通、课堂派）和不同账号的课程，点击批量开始，调度器自动为每门课程分配独立 Worker 线程并行推进；
- **智能信号量控频与风控保护（Anti-Risk Throttling）**：
  - **全局并发上限控制**：支持 1~8 线程并发调节（默认 3 个），充分压榨机器网络性能；
  - **单账号视频流并发锁**：针对同一个账号强制执行单路视频流排队，避免同一账号在同一平台并发播放触发多地登录或刷课异常告警；不同账号之间完全并行；
  - **启动错峰防抖（Stagger Delay）**：避免多个 Worker 瞬间同时向服务器发起握手请求；
- **并发任务看板与精细控制**：
  - 支持多任务卡片并行展示：每个卡片实时呈现其所属平台、账号、课程名、当前小节、独立百分比仪表与停止按钮；
  - 支持对任意单一线程精准取消，或一键终止所有并发线程。

### 7. 系统偏好设置（Preferences）
- 网络代理（HTTP / HTTPS 自定义代理支持）；
- 外部推送通道（PushPlus 微信推送、Bark iOS 推送、自定义 Webhook）；
- Windows 11 Mica 亚克力动态云母材质与系统托盘管理。

---

## 🛠️ 项目结构

```text
FuckRubbishClass/
├── FuckRubbishClass.slnx              # 现代 XML 格式解决方案文件
├── .gitignore                         # .NET / C# 规范排除规则
└── src/
    └── FuckRubbishClass/
        ├── FuckRubbishClass.csproj    # .NET 8.0-windows WPF 项目工程
        ├── App.xaml / App.xaml.cs     # 应用程序入口、Fluent UI 主题加载
        ├── MainWindow.xaml / .cs      # 主窗口（FluentWindow、Mica 底色、导航侧栏）
        ├── Core/
        │   ├── Logging/
        │   │   ├── ILoggerService.cs      # 全局统一日志服务接口
        │   │   ├── LoggerService.cs       # 异步落盘、内存滚动与 UI 派发多 Sink 实现
        │   │   └── AppLogger.cs           # 全局静态日志便捷门面
        │   └── Events/
        │       ├── IEventBus.cs           # 全局统一事件总线接口
        │       ├── EventBus.cs            # 基于 WeakReferenceMessenger 的弱引用总线实现
        │       ├── AppEventBus.cs         # 全局静态事件总线便捷门面
        │       └── DomainEvents.cs        # 账号、课程、任务、模型调度领域事件定义
        ├── Styles/
        │   └── FluentStyles.xaml      # 现代卡片、阴影、层级与排版样式字典
        ├── Converters/
        │   └── Converters.cs          # 颜色、状态、平台标牌、可见性等转换器
        ├── Models/
        │   ├── Enums.cs               # 平台、状态、日志、模型角色与任务类型枚举
        │   ├── AccountModel.cs        # 账号实体（支持多平台、主账号标记、会话凭据）
        │   ├── CourseModel.cs         # 课程实体（进度、测验提示、平台分类）
        │   ├── AiModelConfig.cs       # 统一 AI 模型节点配置（Provider, BaseUrl, Key, 测速状态）
        │   ├── ModelSchedulerConfig.cs# 统一调度策略（角色分工、容灾降级、流式输出）
        │   ├── FeatureModels.cs       # 视频自学、平时测试、考试、签到与 AI 课参数模型
        │   ├── TaskJobModel.cs        # 任务流水线作业模型
        │   ├── LogEntry.cs            # 彩色日志流条目模型
        │   └── AppSettings.cs         # 系统全局偏好（代理、推送、外观）
        ├── Services/
        │   ├── IAccountService.cs     # 账号持久化与状态检查服务
        │   ├── IModelSchedulerService.cs # 模型池管理、并发测速与角色智能调度服务
        │   ├── ICourseService.cs      # 跨平台课程聚合与同步服务
        │   ├── ISettingsService.cs    # 统一参数存储服务 (unified_config.json)
        │   ├── ITaskExecutionService.cs # 异步任务队列、进度分发与终端日志流服务
        │   └── IPythonBridgeService.cs# 与原生/外部 Python Bridge 通信适配器
        ├── ViewModels/
        │   ├── ViewModelBase.cs       # MVVM 基础抽象（Busy/Error/Success 响应式）
        │   ├── MainViewModel.cs       # 主视窗、全局状态栏与路由调度中心
        │   ├── DashboardViewModel.cs  # 工作台总览、关键指标统计、快捷启动入口
        │   ├── AccountsViewModel.cs   # 账号列表、添加账号、扫码授权与会话保活
        │   ├── CourseHubViewModel.cs  # 跨平台课程列表、多维过滤与批量操作
        │   ├── FeaturesViewModel.cs   # 统一功能接入（视频、测验、考试、签到、AI课）
        │   ├── ModelCenterViewModel.cs# 模型池增删改查、连通性测速、分工角色绑定
        │   ├── TaskMonitorViewModel.cs# 实时作业监控、控制台终端与过滤复制
        │   └── SettingsViewModel.cs   # 代理网络、推送通道与外观配置
        └── Views/
            ├── DashboardView.xaml     # 总览视图
            ├── AccountsView.xaml      # 账号管理视图
            ├── CourseHubView.xaml     # 课程中心视图
            ├── FeaturesView.xaml      # 统一功能接入视图
            ├── ModelCenterView.xaml   # 模型配置与调度中心视图
            ├── TaskMonitorView.xaml   # 任务监控与日志控制台视图
            └── SettingsView.xaml      # 系统设置视图
```

---

## 🚀 编译与运行

### 环境要求
- Windows 10 (1809+) 或 Windows 11
- [.NET 8.0 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) 或更高版本

### 编译步骤
```powershell
# 进入项目目录
cd C:\Users\xiaoyu\source\repos\FuckRubbishClass

# 还原依赖与编译
dotnet build FuckRubbishClass.slnx -c Release

# 启动应用程序
dotnet run --project src\FuckRubbishClass\FuckRubbishClass.csproj
```

---

## 🎯 重构效果对比

| 维度 | 原 CourseHub.Desktop (重构前) | FuckRubbishClass (重构后) |
| :--- | :--- | :--- |
| **UI 风格** | 传统 WPF 窗口，控件硬编码堆叠，视觉割裂 | 原生 **Windows 11 Fluent Design**，Mica 云母动态材质，圆角与层次阴影 |
| **模型配置** | 各平台各自填写 API，配置重复且无统一管理 | **全局统一模型池**，支持 DeepSeek、GPT-4o、Moonshot、Ollama 等任意标准接口 |
| **模型调度** | 无调度概念，一个 API 负责所有场景 | **4 维分工角色映射**（快速做题 / 视觉识图 / 深度推理 / 课件解析）+ 自动容灾降级 |
| **连通性诊断** | 无法得知模型是否可用，出错直接崩溃 | **实时测速与连通性测试（Ping）**，毫秒级响应展示与节点健康检查 |
| **账号管理** | 无统一账号列表，Cookie 散落各处，单账号局限 | **跨平台账号卡片列表**，多账号随时切换主账号，独立会话刷新与扫码弹窗 |
| **功能组织** | 按三个上游平台切选项卡，各选项卡功能互不相通 | **统一功能接入工作台**（视频自学 / 平时测试 / 考试中心 / 考勤签到 / AI智能课） |
| **安全机制** | 测验可能误点交卷导致不可逆失分 | **分层保护机制**（本地题库 -> 答案图 -> AI 兜底 -> 默认草稿保存防误交卷） |
| **日志系统** | 临时单文本框追加，易卡顿无落盘，各模块强行耦合 | **工业级全局统一日志系统**（内存UI队列滚动截断 + 后台独立线程异步落盘 `logs/` + 静态/DI双门面） |
| **事件系统** | 各类回调交叉绑定，代码紧密耦合，极易产生内存泄漏 | **全局强类型弱引用事件总线**（领域事件驱动 + 自动 UI Dispatcher 派发 + 弱引用防泄漏） |
| **多线程并发** | 单一同步线程串行阻塞，多平台互斥，无法并行刷课 | **多平台多账号多线程并发调度引擎**（支持 1~8 线程并发，跨账号无缝并行，单账号智能排队防风控） |
| **考试模式** | 仅单一方面，风险不可控 | **提供 100% 安全只读 AI 辅助模式** 与全自动答题双模式选择 |
| **代码架构** | 单一 1475 行 MainWindow.xaml.cs 耦合逻辑 | **严格标准 MVVM 架构**，依赖注入/服务解耦，高内聚低耦合 |

---

## 📜 许可证

本项目仅供计算机系统与现代桌面端架构重构学习研究使用。
