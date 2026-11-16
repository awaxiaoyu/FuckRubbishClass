using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace FuckRubbishClass.ViewModels;

public partial class AccountsViewModel : ViewModelBase
{
    private readonly IAccountService _accountService;
    private readonly ITaskExecutionService _taskService;
    private readonly IPlatformBackendService _backend;
    private readonly ICourseService _courses;
    private CancellationTokenSource? _loginCancellation;
    private AccountModel? _editingAccount;
    public string AccountFormTitle => _editingAccount == null ? "添加平台账号" : "编辑平台账号";

    [ObservableProperty]
    private ImageSource? _qrImage;

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

    public AccountsViewModel(IAccountService accountService, ITaskExecutionService taskService, IPlatformBackendService backend, ICourseService courses)
    {
        _accountService = accountService;
        _taskService = taskService;
        _backend = backend;
        _courses = courses;
        SelectedAccount = _accountService.Accounts.FirstOrDefault();
    }

    [RelayCommand]
    private void StartAddAccount()
    {
        _editingAccount = null;
        OnPropertyChanged(nameof(AccountFormTitle));
        ClearMessages();
        NewAccountPlatform = PlatformType.Zhihuishu;
        NewAccountUsername = string.Empty;
        NewAccountDisplayName = string.Empty;
        NewAccountSchool = string.Empty;
        NewAccountPassword = string.Empty;
        IsAddingAccount = true;
    }

    [RelayCommand]
    private void EditAccount(AccountModel? account)
    {
        if (account == null) return;
        ClearMessages();
        _editingAccount = account;
        NewAccountPlatform = account.Platform;
        NewAccountUsername = account.Username;
        NewAccountDisplayName = account.DisplayName;
        NewAccountSchool = account.School;
        NewAccountPassword = "";
        IsAddingAccount = true;
        OnPropertyChanged(nameof(AccountFormTitle));
    }

    [RelayCommand]
    private void CancelAddAccount()
    {
        IsAddingAccount = false;
        _editingAccount = null;
        NewAccountPassword = "";
    }

    [RelayCommand]
    private async Task ConfirmAddAccountAsync()
    {
        if (string.IsNullOrWhiteSpace(NewAccountUsername))
        {
            ErrorMessage = "请填写账号或手机号";
            return;
        }

        var editing = _editingAccount != null;
        if (editing && _editingAccount!.Platform != NewAccountPlatform)
        { ErrorMessage = "已有账号的平台不能更改，请为其他平台添加新账号。"; return; }
        var acc = _editingAccount ?? new AccountModel
        {
            IsPrimary = !Accounts.Any(x => x.Platform == NewAccountPlatform)
        };
        acc.Platform = NewAccountPlatform;
        acc.Username = NewAccountUsername.Trim();
        acc.DisplayName = string.IsNullOrWhiteSpace(NewAccountDisplayName) ? acc.Username : NewAccountDisplayName.Trim();
        acc.School = NewAccountSchool.Trim();
        acc.Status = AccountStatus.NeedLogin;
        acc.StatusDescription = "账号已保存，请点击登录验证会话";

        if (!string.IsNullOrWhiteSpace(NewAccountPassword))
        {
            acc.ExtraProperties["Password"] = NewAccountPassword;
        }

        if (editing)
        {
            _backend.ReleaseAccount(acc.Id);
            await _accountService.SaveAccountsAsync();
        }
        else await _accountService.AddAccountAsync(acc);
        SelectedAccount = acc;
        IsAddingAccount = false;
        _editingAccount = null;
        NewAccountPassword = "";
        SuccessMessage = $"账号已保存：{acc.DisplayTitle}";
        _taskService.Log(LogLevel.Success, "账号中心", $"已保存账号 [{acc.PlatformDisplayName}] {acc.DisplayTitle}");
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
    private Task TriggerQrLoginAsync(PlatformType platform) => LoginAccountAsync(new AccountModel
    {
        Platform = platform, Username = "扫码账号", DisplayName = "扫码账号",
        Status = AccountStatus.NeedLogin,
        IsPrimary = !Accounts.Any(a => a.Platform == platform)
    });

    [RelayCommand]
    private async Task LoginAccountAsync(AccountModel? account)
    {
        account ??= SelectedAccount;
        if (account == null || _loginCancellation != null) return;
        ClearMessages();
        using var cancellation = new CancellationTokenSource();
        _loginCancellation = cancellation;
        QrPlatformTitle = $"{account.PlatformDisplayName} 登录";
        QrStatusText = account.Platform == PlatformType.Chaoxing
            ? "请在打开的浏览器中完成登录或验证码，验证通过后会自动返回。"
            : "正在连接平台，请等待实际登录结果…";
        QrImage = null;
        IsQrLoginVisible = true;
        account.Status = AccountStatus.LoggingIn;
        var events = new Progress<JsonElement>(value =>
        {
            if (value.TryGetProperty("message", out var message)) QrStatusText = message.GetString() ?? "";
            if (value.TryGetProperty("qrImageBase64", out var image))
            {
                using var stream = new MemoryStream(Convert.FromBase64String(image.GetString()!));
                var bitmap = new BitmapImage();
                bitmap.BeginInit();
                bitmap.CacheOption = BitmapCacheOption.OnLoad;
                bitmap.StreamSource = stream;
                bitmap.EndInit();
                bitmap.Freeze();
                QrImage = bitmap;
            }
        });
        try
        {
            var result = await _backend.InvokeAsync(account,
                account.Platform == PlatformType.Ketangpai ? "course_catalog" : "login",
                new { username = account.Username, password = account.ExtraProperties.GetValueOrDefault("Password", "") },
                value => ((IProgress<JsonElement>)events).Report(value), cancellation.Token);
            if (account.Platform != PlatformType.Ketangpai && !result.GetProperty("result").GetBoolean())
                throw new BackendOperationException("平台未确认登录成功。");
            account.Status = AccountStatus.Active;
            account.StatusDescription = "平台已确认登录成功";
            account.LastActiveTime = DateTime.Now;
            if (!Accounts.Contains(account)) await _accountService.AddAccountAsync(account);
            else await _accountService.SaveAccountsAsync();
            SelectedAccount = account;
            IsQrLoginVisible = false;
            SuccessMessage = $"{account.DisplayTitle} 登录成功";
            try { await _courses.RefreshCoursesForAccountAsync(account.Id); }
            catch (Exception ex) { ErrorMessage = $"已登录，课程同步失败：{ex.Message}"; }
        }
        catch (OperationCanceledException)
        {
            account.Status = AccountStatus.NeedLogin;
            account.StatusDescription = "登录已取消";
        }
        catch (Exception ex)
        {
            account.Status = AccountStatus.NeedLogin;
            account.StatusDescription = ex.Message;
            QrStatusText = ex.Message;
            ErrorMessage = ex.Message;
            _taskService.Log(LogLevel.Error, "登录", ex.Message);
        }
        finally
        {
            _loginCancellation = null;
            if (Accounts.Contains(account)) await _accountService.SaveAccountsAsync();
        }
    }

    [RelayCommand]
    private void CloseQrLogin()
    {
        _loginCancellation?.Cancel();
        IsQrLoginVisible = false;
    }

    public void CancelLogin() => _loginCancellation?.Cancel();
}
