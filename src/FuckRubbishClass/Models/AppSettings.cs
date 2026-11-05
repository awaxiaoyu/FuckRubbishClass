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
