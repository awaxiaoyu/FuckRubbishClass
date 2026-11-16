using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace FuckRubbishClass.Services;

public interface IPlatformBackendService : IDisposable
{
    Task<bool> CheckAvailableAsync();
    Task<JsonElement> InvokeAsync(AccountModel account, string operation, object? arguments = null,
        Action<JsonElement>? onProgress = null, CancellationToken token = default);
    Task<IReadOnlyList<CourseModel>> GetCoursesAsync(AccountModel account, CancellationToken token = default);
    Task RunCourseAsync(AccountModel account, CourseModel course, FeatureType feature, IProgress<double> progress,
        IProgress<string> step, CancellationToken token);
    void ReleaseAccount(string accountId);
}

/// <summary>Maps the desktop contracts to the existing platform adapters without exposing Python to ViewModels.</summary>
public sealed class PlatformBackendService(
    ISettingsService settings, IModelSchedulerService models,
    ILoggerService? logger = null) : IPlatformBackendService
{
    private readonly ILoggerService _logger = logger ?? AppLogger.Instance;
    private readonly ConcurrentDictionary<string, Connection> _connections = new();

    private sealed class Connection(PythonBridgeService bridge)
    {
        public PythonBridgeService Bridge { get; } = bridge;
        public SemaphoreSlim Gate { get; } = new(1, 1);
    }

    public async Task<bool> CheckAvailableAsync()
    {
        using var probe = new PythonBridgeService(_logger);
        return await probe.EnsureConnectedAsync();
    }

    public static string PlatformId(PlatformType platform) => platform switch
    {
        PlatformType.Zhihuishu => "zhihuishu",
        PlatformType.Chaoxing => "chaoxing",
        PlatformType.Ketangpai => "ketangpai",
        _ => throw new NotSupportedException("尚未接入该平台。")
    };

    public async Task<JsonElement> InvokeAsync(AccountModel account, string operation, object? arguments = null,
        Action<JsonElement>? onProgress = null, CancellationToken token = default)
    {
        var platform = PlatformId(account.Platform);
        // Hashing keeps imported account identifiers from becoming filesystem paths.
        var directory = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(account.Id))).ToLowerInvariant();
        var connection = _connections.GetOrAdd(account.Id, _ => new Connection(new PythonBridgeService(_logger,
            dataDirectory: Path.Combine(AppContext.BaseDirectory, "data", "backend", directory))));
        await connection.Gate.WaitAsync(token);
        try
        {
            await connection.Bridge.SendAsync(new { operation = "save_settings", settings = BuildSettings(account) },
                cancellationToken: token);
            var request = arguments == null ? new JsonObject() : JsonSerializer.SerializeToNode(arguments)!.AsObject();
            request["operation"] = operation;
            request["platform"] = platform;
            var result = await connection.Bridge.SendAsync(request, value =>
            {
                if (value.TryGetProperty("level", out var level) && value.TryGetProperty("message", out var message))
                {
                    if (level.GetString() == "error") _logger.Error(platform, message.GetString() ?? "");
                    else if (level.GetString() == "warning") _logger.Warn(platform, message.GetString() ?? "");
                }
                onProgress?.Invoke(value);
            }, token);
            return result ?? throw new IOException("后端没有返回结果。");
        }
        finally { connection.Gate.Release(); }
    }

    private object BuildSettings(AccountModel account)
    {
        var app = settings.AppSettings;
        var video = settings.VideoConfig;
        var homework = settings.HomeworkConfig;
        var ai = settings.AiCourseConfig;
        var fast = models.ResolveModel(ModelRole.FastSolver);
        var deep = models.ResolveModel(ModelRole.DeepReasoning);
        var document = models.ResolveModel(ModelRole.DocumentParser);
        static bool Configured(AiModelConfig? model) => model != null && !string.IsNullOrWhiteSpace(model.ApiKey);
        object Provider(AiModelConfig? model) => new
        {
            api_base = model?.BaseUrl ?? "https://api.openai.com/v1",
            api_key = model?.ApiKey ?? "",
            model_name = model?.ModelId ?? "gpt-4o-mini"
        };
        var result = new Dictionary<string, object?>
        {
            ["general"] = new
            {
                save_cookies = true, cookie_path = "cookies.json", speed = video.PlaybackRate,
                threshold = video.CompletionThreshold, limit = video.DailyLimitMinutes,
                proxies = new { http = app.EnableProxy ? app.HttpProxy : "", https = app.EnableProxy ? app.HttpsProxy : "" },
                pushplus = new { enable = app.EnablePushPlus, token = app.PushPlusToken },
                bark = new { enable = app.EnableBark, token = app.BarkToken }
            },
            ["ai"] = new
            {
                enabled = ai.Enabled && (ai.UseBuiltinPlatformAi || Configured(fast)),
                use_zhidao_ai = ai.UseBuiltinPlatformAi,
                openai = Provider(fast), use_stream = models.SchedulerConfig.StreamResponse,
                ppt_processing = new
                {
                    provide_to_ai = ai.ParseCoursewarePpt && Configured(document),
                    moonShot = new { base_url = document?.BaseUrl ?? "https://api.moonshot.cn/v1",
                        api_key = document?.ApiKey ?? "", delete_after_convert = ai.DeleteUploadedPptAfterParsing }
                }
            }
        };
        if (account.Platform == PlatformType.Zhihuishu)
            result["zhihuishu"] = new { homework = new
            {
                enabled = Configured(fast), api_base = fast?.BaseUrl ?? "", api_key = fast?.ApiKey ?? "",
                model_name = fast?.ModelId ?? "", answer_delay = homework.AnswerDelaySeconds,
                auto_submit = homework.AutoSubmit && !homework.SubmitSafeguard,
                overwrite_with_verified = homework.OverwriteWithVerifiedAnswers
            } };
        if (account.Platform == PlatformType.Chaoxing)
            result["chaoxing"] = new
            {
                browser = video.BrowserType == "headless" ? "chromium" : video.BrowserType,
                save_session = true, playback_rate = video.PlaybackRate,
                muted = video.IsMuted, stall_timeout = video.StallTimeoutSeconds,
                exam_assist = new
                {
                    enabled = Configured(deep), api_base = deep?.BaseUrl ?? "https://api.openai.com/v1",
                    api_key = deep?.ApiKey ?? "", model_name = deep?.ModelId ?? "gpt-4o-mini",
                    timeout = deep?.TimeoutSeconds ?? 60, max_chars = settings.ExamConfig.MaxContentChars
                },
                exam_auto = new
                {
                    enabled = Configured(fast) && settings.ExamConfig.Mode == "AutoExam",
                    api_base = fast?.BaseUrl ?? "https://api.openai.com/v1", api_key = fast?.ApiKey ?? "",
                    model_name = fast?.ModelId ?? "gpt-4o-mini", auto_submit = settings.ExamConfig.AutoSubmitExam,
                    captcha_timeout = settings.ExamConfig.CaptchaTimeoutSeconds,
                    answer_delay = settings.ExamConfig.AnswerDelaySeconds
                }
            };
        if (account.Platform == PlatformType.Ketangpai)
        {
            var attendance = settings.SignInConfig;
            var password = account.ExtraProperties.GetValueOrDefault("Password", "");
            var digest = SHA256.HashData(Encoding.UTF8.GetBytes(account.Id));
            result["ketangpai"] = new Dictionary<string, object?>
            {
                ["WEBSOCKET_SERVER_PORT"] = 20000 + BitConverter.ToUInt16(digest, 0) % 30000,
                ["WEBSOCKET_SERVER_PATH"] = "/" + Convert.ToHexString(digest).ToLowerInvariant(),
                ["USERS"] = string.IsNullOrWhiteSpace(password) ? Array.Empty<object>() :
                    new object[] { new { friendlyName = account.DisplayTitle, username = account.Username, password } },
                ["CLASSES"] = attendance.Schedules.Where(s => s.IsEnabled).Select(s => new
                {
                    friendlyName = s.CourseName, classId = s.ClassId, dayOfWeek = s.DayOfWeek,
                    startTime = s.StartTime, endTime = s.EndTime
                }).ToArray(),
                ["DEFAULT_LATITUDE"] = attendance.EnableGpsSpoofing ? attendance.Latitude.ToString(CultureInfo.InvariantCulture) : "",
                ["DEFAULT_LONGITUDE"] = attendance.EnableGpsSpoofing ? attendance.Longitude.ToString(CultureInfo.InvariantCulture) : "",
                ["签到_CHECK_INTERVAL_SECONDS"] = attendance.CheckIntervalSeconds
            };
        }
        return result;
    }

    public async Task<IReadOnlyList<CourseModel>> GetCoursesAsync(AccountModel account, CancellationToken token = default)
    {
        var response = await InvokeAsync(account, account.Platform == PlatformType.Ketangpai ? "course_catalog" : "courses",
            new { username = account.Username, password = account.ExtraProperties.GetValueOrDefault("Password", "") }, token: token);
        return response.GetProperty("courses").EnumerateArray().Select(item =>
        {
            var metadata = item.GetProperty("metadata");
            string Text(string key) => metadata.TryGetProperty(key, out var value) ? value.ToString() : "";
            var kind = Text("kind");
            return new CourseModel
            {
                CourseId = item.GetProperty("courseId").GetString() ?? "",
                Name = item.GetProperty("name").GetString() ?? "", Platform = account.Platform,
                AccountId = account.Id, AccountDisplayName = account.DisplayTitle,
                ClassId = Text("class_id"), Teacher = Text("teacher"), School = account.School,
                IsAiCourse = kind == "polymas" || kind.Contains("ai", StringComparison.OrdinalIgnoreCase),
                ModeLabel = string.IsNullOrEmpty(kind) ? "平台课程" : kind, RawMetadata = metadata.Clone()
            };
        }).ToList();
    }

    public async Task RunCourseAsync(AccountModel account, CourseModel course, FeatureType feature, IProgress<double> progress,
        IProgress<string> step, CancellationToken token)
    {
        if (account.Id != course.AccountId || account.Platform != course.Platform)
            throw new InvalidOperationException("所选账号与课程所属账号不一致。");
        if (account.Platform == PlatformType.Ketangpai)
            throw new NotSupportedException("课堂派请使用考勤守护与时间表。");
        if (feature == FeatureType.HomeworkQuiz && account.Platform != PlatformType.Zhihuishu)
            throw new NotSupportedException("平时测试当前接入智慧树；学习通章节作业尚未接入。");
        if (course.RawMetadata == null) throw new InvalidOperationException("课程缺少平台标识，请重新同步课程。");
        var response = await InvokeAsync(account, feature == FeatureType.HomeworkQuiz ? "run_homeworks" : "run", new
        {
            course = new { courseId = course.CourseId, name = course.Name, metadata = course.RawMetadata },
            autoSubmit = settings.HomeworkConfig.AutoSubmit && !settings.HomeworkConfig.SubmitSafeguard,
            autoRedo = settings.HomeworkConfig.AutoRedoIfNotFullScore,
            overwriteVerifiedAnswers = settings.HomeworkConfig.OverwriteWithVerifiedAnswers
        }, value => ReportProgress(value, progress, step), token);
        token.ThrowIfCancellationRequested();
        if (response.TryGetProperty("result", out var summary))
        {
            if (summary.TryGetProperty("cancelled", out var cancelled) && cancelled.ValueKind == JsonValueKind.True)
                throw new OperationCanceledException("后端已取消任务。", token);
            if (summary.TryGetProperty("failed", out var failed) && failed.ValueKind == JsonValueKind.Number && failed.GetInt32() > 0)
                throw new BackendOperationException($"测试处理结束，仍有 {failed.GetInt32()} 项失败，请查看任务日志。");
        }
        step.Report("后端任务已结束；课程完成情况以平台记录为准。");
    }

    public static void ReportProgress(JsonElement value, IProgress<double>? progress, IProgress<string>? step)
    {
        if (value.TryGetProperty("message", out var message)) step?.Report(message.GetString() ?? "");
        if (value.TryGetProperty("current", out var current) && current.TryGetDouble(out var number)
            && value.TryGetProperty("total", out var total) && total.TryGetDouble(out var count) && count > 0)
            progress?.Report(Math.Clamp(number / count * 100, 0, 100));
    }

    public void ReleaseAccount(string accountId)
    {
        if (_connections.TryRemove(accountId, out var connection)) connection.Bridge.Dispose();
    }

    public void Dispose()
    {
        foreach (var key in _connections.Keys) ReleaseAccount(key);
    }
}
