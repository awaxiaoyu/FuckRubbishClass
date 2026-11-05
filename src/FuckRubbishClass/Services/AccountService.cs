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

    public ObservableCollection<AccountModel> Accounts { get; } = new();
    public AccountModel? ActiveAccount { get; set; }

    public AccountService(string? dataDir = null, ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;

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
            _logger.Warn("账号中心", $"读取账号文件失败，将载入初始预设: {ex.Message}");
        }

        SeedDefaultAccounts();
        await SaveAccountsAsync();
    }

    private void SeedDefaultAccounts()
    {
        Accounts.Clear();
        Accounts.Add(new AccountModel
        {
            Platform = PlatformType.Zhihuishu,
            Username = "13800138000",
            DisplayName = "小宇同学",
            School = "厦门大学",
            Status = AccountStatus.Active,
            StatusDescription = "会话活跃 (Cookie已同步)",
            IsPrimary = true,
            CourseCount = 4,
            LastActiveTime = DateTime.Now.AddMinutes(-12)
        });

        Accounts.Add(new AccountModel
        {
            Platform = PlatformType.Chaoxing,
            Username = "20230910088",
            DisplayName = "xiaoyu_cx",
            School = "厦门大学",
            Status = AccountStatus.Active,
            StatusDescription = "会话活跃 (Edge托管中)",
            IsPrimary = true,
            CourseCount = 3,
            LastActiveTime = DateTime.Now.AddMinutes(-35)
        });

        Accounts.Add(new AccountModel
        {
            Platform = PlatformType.Ketangpai,
            Username = "13800138000",
            DisplayName = "课堂派自动签到",
            School = "计算机学院",
            Status = AccountStatus.Active,
            StatusDescription = "监听服务运行中",
            IsPrimary = true,
            CourseCount = 2,
            LastActiveTime = DateTime.Now.AddMinutes(-5)
        });

        ActiveAccount = Accounts.FirstOrDefault();
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

        await Task.Delay(1000); // Simulate check
        target.Status = AccountStatus.Active;
        target.StatusDescription = $"会话正常 (刷新于 {DateTime.Now:HH:mm:ss})";
        target.LastActiveTime = DateTime.Now;
        await SaveAccountsAsync();

        _logger.Success("账号中心", $"会话刷新成功: {target.DisplayTitle}");
        _eventBus.Publish(new AccountStateChangedEvent(target, "会话正常"));
        return true;
    }
}
