using CommunityToolkit.Mvvm.ComponentModel;
using System.Text.Json.Serialization;

namespace FuckRubbishClass.Models;

public partial class AccountModel : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private PlatformType _platform = PlatformType.Zhihuishu;

    [ObservableProperty]
    private string _username = string.Empty;

    [ObservableProperty]
    private string _displayName = string.Empty;

    [ObservableProperty]
    private string _school = string.Empty;

    [ObservableProperty]
    private string _avatarUrl = string.Empty;

    [ObservableProperty]
    private AccountStatus _status = AccountStatus.Offline;

    [ObservableProperty]
    private string _statusDescription = "未登录";

    [ObservableProperty]
    private bool _isPrimary;

    [ObservableProperty]
    private DateTime _lastActiveTime = DateTime.Now;

    [ObservableProperty]
    private int _courseCount;

    [ObservableProperty]
    private string _sessionToken = string.Empty;

    [ObservableProperty]
    private string _credentialPath = string.Empty;

    [ObservableProperty]
    private Dictionary<string, string> _extraProperties = new();

    [JsonIgnore]
    public string PlatformDisplayName => Platform switch
    {
        PlatformType.Zhihuishu => "智慧树 / 知到",
        PlatformType.Chaoxing => "学习通 / 超星",
        PlatformType.Ketangpai => "课堂派",
        _ => "通用平台"
    };

    [JsonIgnore]
    public string PlatformShortName => Platform switch
    {
        PlatformType.Zhihuishu => "知到",
        PlatformType.Chaoxing => "超星",
        PlatformType.Ketangpai => "课堂派",
        _ => "其他"
    };

    [JsonIgnore]
    public string StatusText => Status switch
    {
        AccountStatus.Active => "会话有效",
        AccountStatus.NeedLogin => "需重新扫码",
        AccountStatus.LoggingIn => "正在登录…",
        AccountStatus.Offline => "已离线",
        AccountStatus.Error => "异常失效",
        _ => "未知"
    };

    [JsonIgnore]
    public string DisplayTitle => string.IsNullOrWhiteSpace(DisplayName) 
        ? (string.IsNullOrWhiteSpace(Username) ? "未命名账号" : Username) 
        : DisplayName;
}
