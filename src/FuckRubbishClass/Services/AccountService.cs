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
    public ObservableCollection<AccountModel> Accounts { get; } = new();
    public AccountModel? ActiveAccount { get; set; }

    public AccountService(string? dataDir = null)
    {
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
                    return;
                }
            }
        }
        catch
        {
            // Fallback to initial seeds
        }

        // Default initial presets to showcase the UI immediately
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
        catch
        {
            // Ignore write failures in read-only environments
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
            }
            await SaveAccountsAsync();
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
        }
    }

    public async Task<bool> RefreshSessionAsync(string accountId)
    {
        var target = Accounts.FirstOrDefault(x => x.Id == accountId);
        if (target == null) return false;

        target.Status = AccountStatus.LoggingIn;
        target.StatusDescription = "检查会话状态中…";

        await Task.Delay(1000); // Simulate check
        target.Status = AccountStatus.Active;
        target.StatusDescription = $"会话正常 (刷新于 {DateTime.Now:HH:mm:ss})";
        target.LastActiveTime = DateTime.Now;
        await SaveAccountsAsync();
        return true;
    }
}
