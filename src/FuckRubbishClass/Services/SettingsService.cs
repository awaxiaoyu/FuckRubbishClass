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

    public AppSettings AppSettings { get; } = new();
    public VideoStudyConfig VideoConfig { get; } = new();
    public HomeworkQuizConfig HomeworkConfig { get; } = new();
    public ExamAssessmentConfig ExamConfig { get; } = new();
    public AttendanceSignInConfig SignInConfig { get; } = new();
    public AiCourseConfig AiCourseConfig { get; } = new();

    public SettingsService(string? dataDir = null)
    {
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

        SeedDefaultSignInSchedules();
        await SaveSettingsAsync();
    }

    private void SeedDefaultSignInSchedules()
    {
        SignInConfig.Schedules.Clear();
        SignInConfig.Schedules.Add(new SignInScheduleItem
        {
            CourseName = "计算机网络原理",
            ClassId = "KTP889102",
            DayOfWeek = 2, // 星期二
            StartTime = "08:00",
            EndTime = "09:40"
        });
        SignInConfig.Schedules.Add(new SignInScheduleItem
        {
            CourseName = "软件工程",
            ClassId = "KTP992144",
            DayOfWeek = 4, // 星期四
            StartTime = "14:00",
            EndTime = "15:40"
        });
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
        }
        catch
        {
            // Ignore write failures in restricted environments
        }
    }
}
