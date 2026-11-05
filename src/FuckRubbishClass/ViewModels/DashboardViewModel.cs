using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.ViewModels;

public partial class DashboardViewModel : ViewModelBase
{
    private readonly IAccountService _accountService;
    private readonly ICourseService _courseService;
    private readonly IModelSchedulerService _schedulerService;
    private readonly ITaskExecutionService _taskService;
    private readonly IEventBus _eventBus;

    public ObservableCollection<AccountModel> Accounts => _accountService.Accounts;
    public ObservableCollection<CourseModel> Courses => _courseService.Courses;
    public ObservableCollection<LogEntry> RecentLogs => _taskService.Logs;

    public int AccountCount => _accountService.Accounts.Count;
    public int CourseCount => _courseService.Courses.Count;
    public int ModelCount => _schedulerService.Models.Count;
    public int OnlineModelCount => _schedulerService.Models.Count(x => x.IsHealthy);

    public AccountModel? PrimaryAccount => _accountService.ActiveAccount ?? _accountService.Accounts.FirstOrDefault();
    public TaskJobModel? CurrentJob => _taskService.CurrentRunningJob;

    public Action<string>? RequestNavigate { get; set; }

    public DashboardViewModel(
        IAccountService accountService,
        ICourseService courseService,
        IModelSchedulerService schedulerService,
        ITaskExecutionService taskService,
        IEventBus? eventBus = null)
    {
        _accountService = accountService;
        _courseService = courseService;
        _schedulerService = schedulerService;
        _taskService = taskService;
        _eventBus = eventBus ?? AppEventBus.Instance;

        _accountService.Accounts.CollectionChanged += (_, _) => UpdateMetrics();
        _courseService.Courses.CollectionChanged += (_, _) => UpdateMetrics();
        _schedulerService.Models.CollectionChanged += (_, _) => UpdateMetrics();

        // 订阅全局事件
        _eventBus.Subscribe<PrimaryAccountChangedEvent>(this, _ => UpdateMetrics());
        _eventBus.Subscribe<AccountStateChangedEvent>(this, _ => UpdateMetrics());
        _eventBus.Subscribe<TaskJobStartedEvent>(this, _ => UpdateMetrics());
        _eventBus.Subscribe<TaskJobCompletedEvent>(this, _ => UpdateMetrics());
        _eventBus.Subscribe<ModelHealthTestedEvent>(this, _ => UpdateMetrics());
    }

    private void UpdateMetrics()
    {
        OnPropertyChanged(nameof(AccountCount));
        OnPropertyChanged(nameof(CourseCount));
        OnPropertyChanged(nameof(ModelCount));
        OnPropertyChanged(nameof(OnlineModelCount));
        OnPropertyChanged(nameof(PrimaryAccount));
        OnPropertyChanged(nameof(CurrentJob));
    }

    [RelayCommand]
    private void NavigateTo(string pageKey)
    {
        RequestNavigate?.Invoke(pageKey);
        _eventBus.Publish(new NavigationRequestedEvent(pageKey));
    }

    [RelayCommand]
    private async Task QuickStartVideoAsync()
    {
        NavigateTo("features");
        var first = Courses.FirstOrDefault();
        if (first != null)
        {
            var job = new TaskJobModel
            {
                Title = $"快速自学: {first.Name}",
                Type = FeatureType.VideoStudy,
                Platform = first.Platform,
                CourseName = first.Name
            };
            await _taskService.StartJobAsync(job, async (token, progress, step) =>
            {
                step.Report("进入课程播放列表…");
                await Task.Delay(1000, token);
                progress.Report(40);
                step.Report("正在保持倍速自学播放中…");
                await Task.Delay(1500, token);
                progress.Report(100);
                step.Report("该小节已自学完成！");
            });
        }
    }

    [RelayCommand]
    private async Task QuickStartHomeworkAsync()
    {
        NavigateTo("features");
        var first = Courses.FirstOrDefault(x => x.PendingHomeworkCount > 0) ?? Courses.FirstOrDefault();
        if (first != null)
        {
            var job = new TaskJobModel
            {
                Title = $"一键平时测试: {first.Name}",
                Type = FeatureType.HomeworkQuiz,
                Platform = first.Platform,
                CourseName = first.Name
            };
            await _taskService.StartJobAsync(job, async (token, progress, step) =>
            {
                step.Report("匹配题库与答案图…");
                await Task.Delay(1000, token);
                progress.Report(50);
                step.Report("AI 调度未知题解答…");
                await Task.Delay(1200, token);
                progress.Report(100);
                step.Report("草稿已安全保存！");
            });
        }
    }
}
