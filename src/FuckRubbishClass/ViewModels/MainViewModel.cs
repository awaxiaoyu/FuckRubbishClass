using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;

namespace FuckRubbishClass.ViewModels;

public partial class MainViewModel : ViewModelBase
{
    private readonly IAccountService _accountService;
    private readonly ICourseService _courseService;
    private readonly IModelSchedulerService _schedulerService;
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _taskService;
    private readonly IPythonBridgeService _bridgeService;

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
    private string _bridgeStatusText = "Bridge 运行中 (本地集成)";

    public MainViewModel()
    {
        // Setup Services
        _accountService = new AccountService();
        _courseService = new CourseService();
        _schedulerService = new ModelSchedulerService();
        _settingsService = new SettingsService();
        _taskService = new TaskExecutionService();
        _bridgeService = new PythonBridgeService();

        // Setup ViewModels
        DashboardVM = new DashboardViewModel(_accountService, _courseService, _schedulerService, _taskService);
        AccountsVM = new AccountsViewModel(_accountService, _taskService);
        CourseHubVM = new CourseHubViewModel(_courseService, _taskService, _accountService);
        FeaturesVM = new FeaturesViewModel(_settingsService, _taskService, _accountService, _courseService, _schedulerService);
        ModelCenterVM = new ModelCenterViewModel(_schedulerService, _taskService);
        TaskMonitorVM = new TaskMonitorViewModel(_taskService);
        SettingsVM = new SettingsViewModel(_settingsService, _taskService);

        _currentViewModel = DashboardVM;

        DashboardVM.RequestNavigate = key => Navigate(key);

        _taskService.Jobs.CollectionChanged += (_, _) => UpdateStatus();
        _accountService.Accounts.CollectionChanged += (_, _) => UpdateStatus();
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

            var bridgeConnected = await _bridgeService.EnsureConnectedAsync();
            BridgeStatusText = bridgeConnected ? "Python Bridge: 在线" : "本地原生模式";

            _taskService.Log(LogLevel.Success, "系统", "FuckRubbishClass 工作台加载完毕，所有服务就绪。");
        }
        catch (Exception ex)
        {
            _taskService.Log(LogLevel.Error, "初始化", $"初始化失败: {ex.Message}");
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

        var running = _taskService.CurrentRunningJob;
        HasActiveTask = running != null && running.Status == TaskExecutionStatus.Running;
        TaskStatusSummary = HasActiveTask
            ? $"运行中: {running!.Title} ({running.ProgressPercent:F0}%)"
            : "空闲就绪";
    }
}
