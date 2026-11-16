# FuckRubbishClass

Windows 桌面课程工作台，统一管理多个课程平台的账号、课程和已接入的学习任务。桌面端使用 C# / WPF，平台适配器使用 Python，课堂派考勤服务使用 TypeScript / Node.js。

[![Latest release](https://img.shields.io/github/v/release/awaxiaoyu/FuckRubbishClass)](https://github.com/awaxiaoyu/FuckRubbishClass/releases/latest)
[![Release workflow](https://github.com/awaxiaoyu/FuckRubbishClass/actions/workflows/release.yml/badge.svg)](https://github.com/awaxiaoyu/FuckRubbishClass/actions/workflows/release.yml)
[![.NET 8](https://img.shields.io/badge/.NET-8-512BD4?logo=dotnet)](https://dotnet.microsoft.com/download/dotnet/8.0)

项目仍在持续开发中。下表描述当前代码实际接入的能力；不同平台的入口和能力并不完全相同。

## 已接入平台

| 平台 | 当前已接入能力 | 主要运行时 |
| --- | --- | --- |
| 智慧树 / 知到 | 扫码登录、课程同步、视频学习、平时测试、答案预览与题库采集、知到 AI 课程 | Python 3.10+ |
| 学习通 | 托管浏览器登录、课程同步、视频学习、考试列表与选定考试作答、当前题面只读解析 | Python 3.10+、Playwright；默认使用本机 Edge |
| 课堂派 | 账号验证、课程目录、考勤服务状态、时间表编辑、考勤守护启动与停止、日志查看 | Python 3.10+、Node.js 18+、pnpm |

工作台提供视频、平时测试、考试、考勤和 AI 课程面板。任务进度和错误会显示在任务监控中。当前学习通章节作业尚未接入；模型自动 fallback 和独立 Vision 路由也尚未接入后端。更完整的接入范围、配置项和运行细节见 [后端接入与运行说明](BACKEND.md)。

## 下载运行

从 [GitHub Releases](https://github.com/awaxiaoyu/FuckRubbishClass/releases/latest) 下载最新的 `win-x64.zip`，解压到可写目录后运行 `FuckRubbishClass.exe`。发布包已包含 .NET 运行时，无需另装 .NET Desktop Runtime。

使用平台后端前需要安装 Python 3.10 或更高版本，并确保 `python` 或 `py -3` 可从命令行调用。启动应用后，进入“设置 → 后端运行环境”：应用会在每次启动时检查 Python 后端依赖，依赖缺失时显示安装按钮，安装完成后复查并隐藏入口。依赖安装需要访问 PyPI。

使用课堂派还需要安装 Node.js 18+ 和 pnpm，然后在“功能 → 考勤与签到”中安装课堂派服务依赖。该步骤与 Python 后端依赖分开管理。

如需在学习通中使用 Chromium 而不是默认的本机 Edge，先安装 Python 后端依赖，再运行：

```powershell
.\backend\.venv\Scripts\python.exe -m playwright install chromium
```

## 从源码运行

开发环境需要 Windows、.NET 8 SDK 和 Python 3.10+。仅使用智慧树、知到和学习通时，可跳过课堂派 Node 依赖：

```powershell
git clone https://github.com/awaxiaoyu/FuckRubbishClass.git
cd FuckRubbishClass

# 创建 backend/.venv 并安装 Python 依赖
.\scripts\Setup-Backend.ps1 -SkipKetangpai

# 构建并运行桌面端
dotnet build .\src\FuckRubbishClass\FuckRubbishClass.csproj --configuration Release
dotnet run --project .\src\FuckRubbishClass\FuckRubbishClass.csproj
```

如果需要开发或测试课堂派适配器，安装 Node.js 18+ 与 pnpm 后运行：

```powershell
.\scripts\Setup-Backend.ps1
```

安装脚本默认访问 PyPI 和 npm registry。更多 Python 解释器选择、环境变量和后端调试信息见 [BACKEND.md](BACKEND.md)。

## 项目结构

```text
src/FuckRubbishClass/             WPF 桌面端、ViewModel 与 C# 服务
backend/app/                      Python 平台适配器与业务服务
backend/integrations/ketangpai/   课堂派 TypeScript/Node.js sidecar
scripts/Setup-Backend.ps1         创建 Python 虚拟环境并安装后端依赖
tests/                            WPF UI smoke 与 C# Bridge smoke
backend/tests/                    Python 和课堂派相关测试
```

## 本地数据与隐私

应用数据默认保存在程序目录下的 `data/`：账号信息位于 `accounts.json`，界面和模型配置位于 `unified_config.json`，平台会话、缓存和后端日志按账号隔离保存。当前账号凭据、Cookie 和模型 API Key 存储在本地文件中，尚未加密；请将数据目录视为敏感信息，并避免把其中内容上传到 Issue、Pull Request 或公开网盘。

使用模型辅助功能时，相关题目内容和必要上下文会发送给你在模型中心配置的服务商。请先确认所选服务商的数据处理方式符合你的要求。

## 开发与测试

常用检查命令：

```powershell
dotnet build .\src\FuckRubbishClass\FuckRubbishClass.csproj --configuration Release
dotnet run --project .\tests\UiSmoke\UiSmoke.csproj --configuration Release -- .\artifacts\ui-preview
dotnet run --project .\tests\BridgeSmoke\BridgeSmoke.csproj -- .\backend .\backend\.venv\Scripts\python.exe

Push-Location .\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
Pop-Location

Push-Location .\backend\integrations\ketangpai
pnpm exec tsc --noEmit
Pop-Location
node --test .\backend\tests\ketangpai-attendance.test.cjs
```

UI smoke 会使用本地 fixture 数据，不登录真实平台。涉及平台协议或登录流程的改动，请同时添加可离线运行的测试，并在提交说明中注明尚未覆盖的真实平台验证。

## 发布

Windows x64 发布由 GitHub Actions 的 [Build and publish release](https://github.com/awaxiaoyu/FuckRubbishClass/actions/workflows/release.yml) workflow 构建。维护者可在 Actions 页面手动运行该 workflow 并填写 `vMAJOR.MINOR.PATCH` 格式的 tag；成功后会创建 GitHub Release 并上传 ZIP 包。

## 参与贡献

欢迎提交 Bug 报告、功能建议和 Pull Request。报告问题时请写明应用版本、Windows 版本、复现步骤和脱敏后的错误日志；请勿附带账号密码、Cookie、API Key、个人课程信息或包含这些内容的截图。提交代码前至少运行与改动相关的构建和测试，并说明测试结果。

## 许可证

仓库根目录目前没有项目级 `LICENSE`，因此整个仓库的授权范围尚未确定。`backend/LICENSE` 中的 MIT License 不应被视为自动覆盖仓库其他目录。在补充根目录许可证前，请不要假定整个项目已采用某种开源许可证；正式开放代码复用前需要由维护者确定并添加项目级许可证。
