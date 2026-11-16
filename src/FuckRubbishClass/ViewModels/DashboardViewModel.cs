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
    private void QuickStartVideo()
    {
        NavigateTo("courses");
        _taskService.Log(LogLevel.Info, "快捷入口", "请选择已同步的课程，再启动自学任务。");
    }

    [RelayCommand]
    private void QuickStartHomework()
    {
        NavigateTo("features");
        _taskService.Log(LogLevel.Info, "快捷入口", "请选择智慧树课程并检查作业参数，再启动平时测试。");
    }
}