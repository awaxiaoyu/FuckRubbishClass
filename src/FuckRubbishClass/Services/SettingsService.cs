using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.IO;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface ISettingsService
{
    AppSettings AppSettings { get; }
    VideoStudyConfig VideoConfig { get; }
    HomeworkQuizConfig HomeworkConfig { get; }
    ExamAssessmentConfig ExamConfig { get; }
    AttendanceSignInConfig SignInConfig { get; }
    AiCourseConfig AiCourseConfig { get; }
    Task LoadSettingsAsync();
    Task SaveSettingsAsync();
}

public class SettingsService : ISettingsService
{
    private readonly string _filePath;
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;

    public AppSettings AppSettings { get; } = new();
    public VideoStudyConfig VideoConfig { get; } = new();
    public HomeworkQuizConfig HomeworkConfig { get; } = new();
    public ExamAssessmentConfig ExamConfig { get; } = new();
    public AttendanceSignInConfig SignInConfig { get; } = new();
    public AiCourseConfig AiCourseConfig { get; } = new();

    public SettingsService(string? dataDir = null, ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;

        var dir = dataDir ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "unified_config.json");
    }

    public async Task LoadSettingsAsync()
    {
        try
        {
            if (File.Exists(_filePath))
            {
                var json = await File.ReadAllTextAsync(_filePath);
                using var doc = JsonDocument.Parse(json);

                if (doc.RootElement.TryGetProperty("app", out var appElem))
                {
                    var loaded = JsonSerializer.Deserialize<AppSettings>(appElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, AppSettings);
                }

                if (doc.RootElement.TryGetProperty("video", out var vElem))
                {
                    var loaded = JsonSerializer.Deserialize<VideoStudyConfig>(vElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, VideoConfig);
                }

                if (doc.RootElement.TryGetProperty("homework", out var hwElem))
                {
                    var loaded = JsonSerializer.Deserialize<HomeworkQuizConfig>(hwElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, HomeworkConfig);
                }

                if (doc.RootElement.TryGetProperty("exam", out var exElem))
                {
                    var loaded = JsonSerializer.Deserialize<ExamAssessmentConfig>(exElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, ExamConfig);
                }

                if (doc.RootElement.TryGetProperty("signin", out var sElem))
                {
                    var loaded = JsonSerializer.Deserialize<AttendanceSignInConfig>(sElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, SignInConfig);
                }

                if (doc.RootElement.TryGetProperty("aicourse", out var aiElem))
                {
                    var loaded = JsonSerializer.Deserialize<AiCourseConfig>(aiElem.GetRawText());
                    if (loaded != null) CopyProps(loaded, AiCourseConfig);
                }
                return;
            }
        }
        catch
        {
            // Fallback to defaults
        }

        SignInConfig.Schedules.Clear();
        await SaveSettingsAsync();
    }

    private static void CopyProps<T>(T source, T target)
    {
        foreach (var prop in typeof(T).GetProperties().Where(p => p.CanWrite && p.CanRead))
        {
            var val = prop.GetValue(source);
            if (val != null) prop.SetValue(target, val);
        }
    }

    public async Task SaveSettingsAsync()
    {
        try
        {
            var data = new
            {
                app = AppSettings,
                video = VideoConfig,
                homework = HomeworkConfig,
                exam = ExamConfig,
                signin = SignInConfig,
                aicourse = AiCourseConfig
            };
            var json = JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(_filePath, json);

            _logger.Success("配置中心", "全局统一配置文件已更新 (unified_config.json)。");
            _eventBus.Publish(new ConfigSavedEvent("unified"));
        }
        catch (Exception ex)
        {
            _logger.Error("配置中心", "保存统一配置失败", ex);
        }
    }
}
