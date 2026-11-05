using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.ViewModels;

public partial class AccountsViewModel : ViewModelBase
{
    private readonly IAccountService _accountService;
    private readonly ITaskExecutionService _taskService;

    public ObservableCollection<AccountModel> Accounts => _accountService.Accounts;

    [ObservableProperty]
    private AccountModel? _selectedAccount;

    [ObservableProperty]
    private string _filterPlatform = "全部";

    [ObservableProperty]
    private bool _isAddingAccount;

    // Form inputs for adding account
    [ObservableProperty]
    private PlatformType _newAccountPlatform = PlatformType.Zhihuishu;

    [ObservableProperty]
    private string _newAccountUsername = string.Empty;

    [ObservableProperty]
    private string _newAccountDisplayName = string.Empty;

    [ObservableProperty]
    private string _newAccountSchool = string.Empty;

    [ObservableProperty]
    private string _newAccountPassword = string.Empty;

    // QR Login sheet state
    [ObservableProperty]
    private bool _isQrLoginVisible;

    [ObservableProperty]
    private string _qrStatusText = "正在生成登录二维码…";

    [ObservableProperty]
    private string _qrPlatformTitle = "智慧树扫码登录";

    public AccountsViewModel(IAccountService accountService, ITaskExecutionService taskService)
    {
        _accountService = accountService;
        _taskService = taskService;
        SelectedAccount = _accountService.Accounts.FirstOrDefault();
    }

    [RelayCommand]
    private void StartAddAccount()
    {
        NewAccountPlatform = PlatformType.Zhihuishu;
        NewAccountUsername = string.Empty;
        NewAccountDisplayName = string.Empty;
        NewAccountSchool = string.Empty;
        NewAccountPassword = string.Empty;
        IsAddingAccount = true;
    }

    [RelayCommand]
    private void CancelAddAccount()
    {
        IsAddingAccount = false;
    }

    [RelayCommand]
    private async Task ConfirmAddAccountAsync()
    {
        if (string.IsNullOrWhiteSpace(NewAccountUsername))
        {
            ErrorMessage = "请填写账号或手机号";
            return;
        }

        var acc = new AccountModel
        {
            Platform = NewAccountPlatform,
            Username = NewAccountUsername.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(NewAccountDisplayName) ? NewAccountUsername.Trim() : NewAccountDisplayName.Trim(),
            School = string.IsNullOrWhiteSpace(NewAccountSchool) ? "默认院校" : NewAccountSchool.Trim(),
            Status = AccountStatus.Active,
            StatusDescription = "已手动配置并保存",
            IsPrimary = Accounts.Count(x => x.Platform == NewAccountPlatform) == 0,
            LastActiveTime = DateTime.Now
        };

        if (!string.IsNullOrWhiteSpace(NewAccountPassword))
        {
            acc.ExtraProperties["Password"] = NewAccountPassword;
        }

        await _accountService.AddAccountAsync(acc);
        SelectedAccount = acc;
        IsAddingAccount = false;
        SuccessMessage = $"已成功添加账号：{acc.DisplayTitle}";
        _taskService.Log(LogLevel.Success, "账号中心", $"已添加账号 [{acc.PlatformDisplayName}] {acc.DisplayTitle}");
    }

    [RelayCommand]
    private async Task DeleteAccountAsync(AccountModel? account)
    {
        var target = account ?? SelectedAccount;
        if (target == null) return;

        var name = target.DisplayTitle;
        await _accountService.DeleteAccountAsync(target.Id);
        SelectedAccount = Accounts.FirstOrDefault();
        SuccessMessage = $"已删除账号：{name}";
        _taskService.Log(LogLevel.Info, "账号中心", $"已删除账号 {name}");
    }

    [RelayCommand]
    private async Task SetAsPrimaryAsync(AccountModel? account)
    {
        var target = account ?? SelectedAccount;
        if (target == null) return;

        await _accountService.SetPrimaryAccountAsync(target.Id);
        SuccessMessage = $"已设为 [{target.PlatformDisplayName}] 主账号：{target.DisplayTitle}";
        _taskService.Log(LogLevel.Info, "账号中心", $"切换 [{target.PlatformDisplayName}] 主账号为：{target.DisplayTitle}");
    }

    [RelayCommand]
    private async Task RefreshSessionAsync(AccountModel? account)
    {
        var target = account ?? SelectedAccount;
        if (target == null) return;

        IsBusy = true;
        BusyMessage = $"正在刷新 {target.DisplayTitle} 会话…";
        _taskService.Log(LogLevel.Info, "账号中心", $"正在检查/刷新会话：{target.DisplayTitle}");

        var ok = await _accountService.RefreshSessionAsync(target.Id);
        IsBusy = false;

        if (ok)
        {
            SuccessMessage = $"{target.DisplayTitle} 会话正常";
            _taskService.Log(LogLevel.Success, "账号中心", $"会话有效：{target.DisplayTitle}");
        }
        else
        {
            ErrorMessage = $"{target.DisplayTitle} 会话失效，需重新扫码";
            _taskService.Log(LogLevel.Warning, "账号中心", $"会话失效：{target.DisplayTitle}");
        }
    }

    [RelayCommand]
    private async Task TriggerQrLoginAsync(PlatformType platform)
    {
        QrPlatformTitle = platform switch
        {
            PlatformType.Zhihuishu => "智慧树 / 知到 扫码登录",
            PlatformType.Chaoxing => "学习通 / 超星 浏览器会话授权",
            PlatformType.Ketangpai => "课堂派 授权登录",
            _ => "第三方扫码登录"
        };
        QrStatusText = "正在启动登录通道并生成二维码…";
        IsQrLoginVisible = true;

        _taskService.Log(LogLevel.Info, "扫码登录", $"正在唤起 {QrPlatformTitle}…");

        await Task.Delay(1500);
        QrStatusText = "二维码已生成，请使用手机客户端扫码确认授权。";
    }

    [RelayCommand]
    private void CloseQrLogin()
    {
        IsQrLoginVisible = false;
    }

    [RelayCommand]
    private async Task CompleteQrLoginAsync()
    {
        QrStatusText = "扫码成功，正在同步课程与用户资料…";
        await Task.Delay(1000);

        var newAcc = new AccountModel
        {
            Platform = PlatformType.Zhihuishu,
            Username = "新扫码用户",
            DisplayName = "扫码登录用户",
            School = "厦门大学",
            Status = AccountStatus.Active,
            StatusDescription = "扫码授权会话已就绪",
            IsPrimary = true,
            LastActiveTime = DateTime.Now,
            CourseCount = 3
        };

        await _accountService.AddAccountAsync(newAcc);
        SelectedAccount = newAcc;
        IsQrLoginVisible = false;
        SuccessMessage = "扫码登录成功，已绑定至账号列表！";
        _taskService.Log(LogLevel.Success, "扫码登录", $"扫码登录成功，账号：{newAcc.DisplayTitle}");
    }
}
