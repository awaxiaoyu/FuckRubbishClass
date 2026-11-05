using CommunityToolkit.Mvvm.ComponentModel;

namespace FuckRubbishClass.Models;

public partial class AppSettings : ObservableObject
{
    // 网络代理
    [ObservableProperty]
    private bool _enableProxy;

    [ObservableProperty]
    private string _httpProxy = string.Empty;

    [ObservableProperty]
    private string _httpsProxy = string.Empty;

    // 外部推送通知
    [ObservableProperty]
    private bool _enablePushPlus;

    [ObservableProperty]
    private string _pushPlusToken = string.Empty;

    [ObservableProperty]
    private bool _enableBark;

    [ObservableProperty]
    private string _barkToken = string.Empty;

    [ObservableProperty]
    private bool _enableWebhook;

    [ObservableProperty]
    private string _webhookUrl = string.Empty;

    // 存储与路径
    [ObservableProperty]
    private string _dataDirectory = "data";

    [ObservableProperty]
    private string _cookiesDirectory = "data/cookies";

    [ObservableProperty]
    private string _logsDirectory = "data/logs";

    // 多平台多账号多线程并发调度
    [ObservableProperty]
    private bool _enableConcurrentDispatch = true;

    [ObservableProperty]
    private int _maxConcurrentTasks = 3; // 全局最大并行任务数 (1~8)

    [ObservableProperty]
    private int _maxConcurrentPerAccount = 1; // 单账号并发限制 (推荐1，防平台多地同时播放风控)

    [ObservableProperty]
    private int _staggerDelayMs = 1200; // 并发启动错峰间隔毫秒 (防突发高频请求)

    // 外观偏好
    [ObservableProperty]
    private string _themeMode = "Light"; // Light, Dark, System

    [ObservableProperty]
    private bool _enableMicaBackdrop = true;

    [ObservableProperty]
    private bool _minimizeToTray = true;

    [ObservableProperty]
    private bool _autoCheckUpdates = true;
}
