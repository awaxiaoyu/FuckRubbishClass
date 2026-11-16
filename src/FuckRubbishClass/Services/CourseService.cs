using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface ICourseService
{
    ObservableCollection<CourseModel> Courses { get; }
    Task LoadCoursesAsync();
    Task SaveCoursesAsync();
    Task RefreshCoursesForAccountAsync(string accountId, CancellationToken token = default);
}

public class CourseService : ICourseService
{
    private readonly string _filePath;
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;
    private readonly IAccountService _accounts;
    private readonly IPlatformBackendService _backend;

    public ObservableCollection<CourseModel> Courses { get; } = new();

    public CourseService(IAccountService accounts, IPlatformBackendService backend, string? dataDir = null,
        ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _accounts = accounts;
        _backend = backend;
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;
        var dir = dataDir ?? Path.Combine(AppContext.BaseDirectory, "data");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "courses.json");
    }

    public async Task LoadCoursesAsync()
    {
        Courses.Clear();
        if (!File.Exists(_filePath)) return;
        try
        {
            var loaded = JsonSerializer.Deserialize<List<CourseModel>>(await File.ReadAllTextAsync(_filePath));
            if (loaded != null)
                foreach (var course in loaded.Where(c => _accounts.Accounts.Any(a => a.Id == c.AccountId)))
                {
                    course.IsSelected = false;
                    Courses.Add(course);
                }
        }
        catch (Exception ex) { _logger.Error("课程中心", $"读取课程缓存失败: {ex.Message}", ex); }
    }

    public async Task SaveCoursesAsync()
    {
        await File.WriteAllTextAsync(_filePath,
            JsonSerializer.Serialize(Courses.ToList(), new JsonSerializerOptions { WriteIndented = true }));
    }

    public async Task RefreshCoursesForAccountAsync(string accountId, CancellationToken token = default)
    {
        var account = _accounts.Accounts.FirstOrDefault(a => a.Id == accountId)
            ?? throw new InvalidOperationException("找不到课程所属账号。");
        _logger.Info("课程同步", $"正在同步 [{account.PlatformDisplayName}] {account.DisplayTitle} 的课程…");
        var courses = await _backend.GetCoursesAsync(account, token);
        foreach (var old in Courses.Where(c => c.AccountId == accountId).ToList()) Courses.Remove(old);
        foreach (var course in courses) Courses.Add(course);
        account.CourseCount = courses.Count;
        account.LastActiveTime = DateTime.Now;
        account.Status = AccountStatus.Active;
        account.StatusDescription = $"已同步 {courses.Count} 门真实课程";
        await SaveCoursesAsync();
        await _accounts.SaveAccountsAsync();
        _logger.Success("课程同步", $"{account.DisplayTitle} 已同步 {courses.Count} 门课程。");
        _eventBus.Publish(new CoursesRefreshedEvent(Courses.Count, accountId));
        _eventBus.Publish(new AccountStateChangedEvent(account, account.StatusDescription));
    }
}
