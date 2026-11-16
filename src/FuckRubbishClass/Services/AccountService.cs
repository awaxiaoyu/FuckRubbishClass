using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface IAccountService
{
    ObservableCollection<AccountModel> Accounts { get; }
    AccountModel? ActiveAccount { get; set; }
    Task LoadAccountsAsync();
    Task SaveAccountsAsync();
    Task AddAccountAsync(AccountModel account);
    Task DeleteAccountAsync(string accountId);
    Task SetPrimaryAccountAsync(string accountId);
    Task<bool> RefreshSessionAsync(string accountId);
}

public class AccountService : IAccountService
{
    private readonly string _filePath;
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;
    private readonly IPlatformBackendService _backend;

    public ObservableCollection<AccountModel> Accounts { get; } = new();
    public AccountModel? ActiveAccount { get; set; }

    public AccountService(IPlatformBackendService backend, string? dataDir = null, ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;
        _backend = backend;

        var dir = dataDir ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "accounts.json");
    }

    public async Task LoadAccountsAsync()
    {
        try
        {
            if (File.Exists(_filePath))
            {
                var json = await File.ReadAllTextAsync(_filePath);
                var loaded = JsonSerializer.Deserialize<List<AccountModel>>(json);
                if (loaded != null && loaded.Count > 0)
                {
                    Accounts.Clear();
                    foreach (var acc in loaded)
                    {
                        acc.Status = AccountStatus.NeedLogin;
                        acc.StatusDescription = "会话尚未验证，请刷新会话或登录";
                        Accounts.Add(acc);
                    }
                    ActiveAccount = Accounts.FirstOrDefault(x => x.IsPrimary) ?? Accounts.FirstOrDefault();
                    _logger.Info("账号中心", $"已载入本地 {Accounts.Count} 个账号配置。");
                    return;
                }
            }
        }
        catch (Exception ex)
        {
            _logger.Warn("账号中心", $"读取账号文件失败: {ex.Message}");
        }

        Accounts.Clear();
    }

    public async Task SaveAccountsAsync()
    {
        try
        {
            var json = JsonSerializer.Serialize(Accounts.ToList(), new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(_filePath, json);
        }
        catch (Exception ex)
        {
            _logger.Error("账号中心", "保存账号配置失败", ex);
        }
    }

    public async Task AddAccountAsync(AccountModel account)
    {
        if (account.IsPrimary || Accounts.Count == 0)
        {
            foreach (var a in Accounts.Where(x => x.Platform == account.Platform))
            {
                a.IsPrimary = false;
            }
            account.IsPrimary = true;
        }

        Accounts.Add(account);
        ActiveAccount = account;
        await SaveAccountsAsync();

        _logger.Success("账号中心", $"已添加新账号 [{account.PlatformDisplayName}] {account.DisplayTitle}");
        _eventBus.Publish(new AccountLoggedInEvent(account));
        _eventBus.Publish(new PrimaryAccountChangedEvent(account));
    }

    public async Task DeleteAccountAsync(string accountId)
    {
        var acc = Accounts.FirstOrDefault(x => x.Id == accountId);
        if (acc != null)
        {
            _backend.ReleaseAccount(accountId);
            Accounts.Remove(acc);
            if (ActiveAccount?.Id == accountId)
            {
                ActiveAccount = Accounts.FirstOrDefault();
                if (ActiveAccount != null)
                {
                    _eventBus.Publish(new PrimaryAccountChangedEvent(ActiveAccount));
                }
            }
            await SaveAccountsAsync();

            _logger.Info("账号中心", $"已移除账号: {acc.DisplayTitle}");
            _eventBus.Publish(new AccountDeletedEvent(accountId));
        }
    }

    public async Task SetPrimaryAccountAsync(string accountId)
    {
        var target = Accounts.FirstOrDefault(x => x.Id == accountId);
        if (target != null)
        {
            foreach (var a in Accounts.Where(x => x.Platform == target.Platform))
            {
                a.IsPrimary = (a.Id == target.Id);
            }
            ActiveAccount = target;
            await SaveAccountsAsync();

            _logger.Info("账号中心", $"主账号变更为: [{target.PlatformDisplayName}] {target.DisplayTitle}");
            _eventBus.Publish(new PrimaryAccountChangedEvent(target));
        }
    }

    public async Task<bool> RefreshSessionAsync(string accountId)
    {
        var target = Accounts.FirstOrDefault(x => x.Id == accountId);
        if (target == null) return false;

        target.Status = AccountStatus.LoggingIn;
        target.StatusDescription = "检查会话状态中…";
        _eventBus.Publish(new AccountStateChangedEvent(target, "正在刷新会话"));

        try
        {
            var courses = await _backend.GetCoursesAsync(target);
            target.CourseCount = courses.Count;
            target.Status = AccountStatus.Active;
            target.StatusDescription = $"平台会话已验证 ({DateTime.Now:HH:mm:ss})";
            target.LastActiveTime = DateTime.Now;
            return true;
        }
        catch (Exception ex)
        {
            target.Status = AccountStatus.NeedLogin;
            target.StatusDescription = ex.Message;
            _logger.Warn("账号中心", $"会话检查失败: {ex.Message}");
            return false;
        }
        finally
        {
            await SaveAccountsAsync();
            _eventBus.Publish(new AccountStateChangedEvent(target, target.StatusDescription));
        }
    }
}
