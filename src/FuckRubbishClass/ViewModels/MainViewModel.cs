using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;

namespace FuckRubbishClass.ViewModels;

public partial class MainViewModel : ViewModelBase, IDisposable
{
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;
    private readonly IAccountService _accountService;
    private readonly ICourseService _courseService;
    private readonly IModelSchedulerService _schedulerService;
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _taskService;
    private readonly IPlatformBackendService _backendService;

    public DashboardViewModel DashboardVM { get; }
    public AccountsViewModel AccountsVM { get; }
    public CourseHubViewModel CourseHubVM { get; }
    public FeaturesViewModel FeaturesVM { get; }
    public ModelCenterViewModel ModelCenterVM { get; }
    public TaskMonitorViewModel TaskMonitorVM { get; }
    public SettingsViewModel SettingsVM { get; }

    [ObservableProperty]
    private string _currentPageKey = "dashboard";

    [ObservableProperty]
    private object _currentViewModel;

    [ObservableProperty]
    private string _appTitle = "FuckRubbishClass";

    [ObservableProperty]
    private string _activeAccountSummary = "未选择账号";

    [ObservableProperty]
    private string _taskStatusSummary = "空闲就绪";

    [ObservableProperty]
    private bool _hasActiveTask;

    [ObservableProperty]
    private string _bridgeStatusText = "后端尚未检查";

    public MainViewModel()
    {
        // 1. 初始化全局基础设施 (Logger & EventBus)
        _logger = AppLogger.Instance;
        _eventBus = AppEventBus.Instance;

        // 2. 初始化服务层
        _schedulerService = new ModelSchedulerService(logger: _logger, eventBus: _eventBus);
        _settingsService = new SettingsService(logger: _logger, eventBus: _eventBus);
        _backendService = new PlatformBackendService(_settingsService, _schedulerService, _logger);
        _accountService = new AccountService(_backendService, logger: _logger, eventBus: _eventBus);
        _courseService = new CourseService(_accountService, _backendService, logger: _logger, eventBus: _eventBus);
        _taskService = new TaskExecutionService(_logger, _eventBus);

        // 3. 初始化 ViewModels
        DashboardVM = new DashboardViewModel(_accountService, _courseService, _schedulerService, _taskService, _eventBus);
        AccountsVM = new AccountsViewModel(_accountService, _taskService, _backendService, _courseService);
        CourseHubVM = new CourseHubViewModel(_courseService, _taskService, _accountService, _backendService);
        FeaturesVM = new FeaturesViewModel(_settingsService, _taskService, _accountService, _courseService, _backendService);
        ModelCenterVM = new ModelCenterViewModel(_schedulerService, _taskService);
        TaskMonitorVM = new TaskMonitorViewModel(_taskService, _logger, _eventBus);
        SettingsVM = new SettingsViewModel(_settingsService, _taskService);

        _currentViewModel = DashboardVM;

        // 4. 注册全局事件订阅
        SubscribeGlobalEvents();
    }

    private void SubscribeGlobalEvents()
    {
        // 账号变更事件
        _eventBus.Subscribe<PrimaryAccountChangedEvent>(this, e =>
        {
            ActiveAccountSummary = $"{e.Account.DisplayTitle} ({e.Account.PlatformDisplayName})";
        });

        _eventBus.Subscribe<AccountStateChangedEvent>(this, e =>
        {
            if (_accountService.ActiveAccount?.Id == e.Account.Id)
            {
                ActiveAccountSummary = $"{e.Account.DisplayTitle} ({e.Account.PlatformDisplayName}) · {e.Account.StatusText}";
            }
        });

        // 任务状态与进度事件
        _eventBus.Subscribe<TaskJobStartedEvent>(this, e => UpdateStatus());
        _eventBus.Subscribe<TaskJobProgressEvent>(this, e => UpdateStatus());
        _eventBus.Subscribe<TaskJobCompletedEvent>(this, e => UpdateStatus());

        // 页面跳转请求事件
        _eventBus.Subscribe<NavigationRequestedEvent>(this, e =>
        {
            Navigate(e.PageKey);
        });
    }

    public async Task InitializeAsync()
    {
        IsBusy = true;
        BusyMessage = "正在初始化 FuckRubbishClass Fluent 核心服务…";

        try
        {
            await _settingsService.LoadSettingsAsync();
            await _accountService.LoadAccountsAsync();
            await _courseService.LoadCoursesAsync();
            await _schedulerService.LoadConfigAsync();

            _taskService.MaxConcurrency = _settingsService.AppSettings.EnableConcurrentDispatch
                ? _settingsService.AppSettings.MaxConcurrentTasks : 1;
            _taskService.StaggerDelayMs = _settingsService.AppSettings.StaggerDelayMs;
            _settingsService.AppSettings.MaxConcurrentPerAccount = 1;
            _settingsService.SignInConfig.IsServiceRunning = false;
            AccountsVM.SelectedAccount = _accountService.ActiveAccount;
            var dependencyCheck = SettingsVM.CheckPythonBackendDependenciesAsync();
            var bridgeConnected = await _backendService.CheckAvailableAsync();
            await dependencyCheck;
            BridgeStatusText = bridgeConnected ? "Python 后端：就绪（按账号启动）" : "后端不可用，请查看日志";

            if (bridgeConnected) _logger.Success("系统", "工作台加载完毕，平台后端已就绪。");
            else _logger.Warn("系统", "工作台已加载，平台后端尚不可用，请查看 Bridge 日志。");
        }
        catch (Exception ex)
        {
            _logger.Error("初始化", $"初始化失败: {ex.Message}", ex);
        }
        finally
        {
            IsBusy = false;
            UpdateStatus();
        }
    }

    [RelayCommand]
    public void Navigate(string pageKey)
    {
        CurrentPageKey = pageKey;
        CurrentViewModel = pageKey switch
        {
            "dashboard" => DashboardVM,
            "accounts" => AccountsVM,
            "courses" => CourseHubVM,
            "features" => FeaturesVM,
            "models" => ModelCenterVM,
            "tasks" => TaskMonitorVM,
            "settings" => SettingsVM,
            _ => DashboardVM
        };

        UpdateStatus();
    }

    private void UpdateStatus()
    {
        var primary = _accountService.ActiveAccount ?? _accountService.Accounts.FirstOrDefault();
        ActiveAccountSummary = primary != null
            ? $"{primary.DisplayTitle} ({primary.PlatformDisplayName})"
            : "未登录账号";

        var runningCount = _taskService.ActiveWorkerCount;
        var pendingCount = _taskService.PendingJobs.Count;

        HasActiveTask = runningCount > 0;
        if (runningCount > 0)
        {
            var current = _taskService.RunningJobs.FirstOrDefault();
            var step = current != null ? current.Title : "并发刷课任务";
            TaskStatusSummary = runningCount == 1
                ? $"⚡ 运行中: {step} ({current?.ProgressPercent:F0}%)"
                : $"⚡ 多线程并发中: {runningCount} 个跨平台/多账号任务正在并行 (队列排队: {pendingCount})";
        }
        else if (pendingCount > 0)
        {
            TaskStatusSummary = $"⏳ 队列等待: {pendingCount} 个任务排队中…";
            HasActiveTask = true;
        }
        else
        {
            TaskStatusSummary = "空闲就绪 (并发多线程引擎已待命)";
        }
    }

    public void Dispose()
    {
        AccountsVM.CancelLogin();
        FeaturesVM.CancelPendingOperation();
        _taskService.CancelAllJobs();
        _backendService.Dispose();
        _eventBus.UnsubscribeAll(this);
    }
}
