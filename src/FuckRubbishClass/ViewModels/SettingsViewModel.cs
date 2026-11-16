using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;

namespace FuckRubbishClass.ViewModels;

public partial class SettingsViewModel : ViewModelBase
{
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _taskService;
    private readonly IBackendSetupService _backendSetup;

    [ObservableProperty]
    private bool _isBackendDependencyCheckComplete;

    [ObservableProperty]
    private bool _arePythonBackendDependenciesInstalled;

    public AppSettings Settings => _settingsService.AppSettings;
    public bool ShowBackendSetupPanel => IsBackendDependencyCheckComplete && !ArePythonBackendDependenciesInstalled;

    public SettingsViewModel(ISettingsService settingsService, ITaskExecutionService taskService,
        IBackendSetupService? backendSetup = null)
    {
        _settingsService = settingsService;
        _taskService = taskService;
        _backendSetup = backendSetup ?? new BackendSetupService();
    }

    partial void OnIsBackendDependencyCheckCompleteChanged(bool value) => OnPropertyChanged(nameof(ShowBackendSetupPanel));
    partial void OnArePythonBackendDependenciesInstalledChanged(bool value) => OnPropertyChanged(nameof(ShowBackendSetupPanel));

    public async Task CheckPythonBackendDependenciesAsync()
    {
        IsBackendDependencyCheckComplete = false;
        try
        {
            ArePythonBackendDependenciesInstalled = await _backendSetup.ArePythonDependenciesInstalledAsync();
        }
        catch (Exception ex)
        {
            ArePythonBackendDependenciesInstalled = false;
            _taskService.Log(LogLevel.Warning, "后端依赖", $"依赖状态检查失败：{ex.Message}");
        }
        finally { IsBackendDependencyCheckComplete = true; }
    }

    [RelayCommand]
    private async Task InstallBackendDependenciesAsync()
    {
        if (IsBusy) return;
        ClearMessages();
        IsBusy = true;
        BusyMessage = "正在检查 Python 环境…";
        IProgress<string> progress = new Progress<string>(message => BusyMessage = message);
        try
        {
            await _backendSetup.InstallPythonDependenciesAsync(message => progress.Report(message));
            ArePythonBackendDependenciesInstalled = await _backendSetup.ArePythonDependenciesInstalledAsync();
            if (!ArePythonBackendDependenciesInstalled)
                throw new InvalidOperationException("安装命令已完成，但后端依赖检查仍未通过，请查看安装输出后重试。");
            SuccessMessage = "Python 后端依赖已安装。课堂派 Node 依赖请在考勤页面单独安装。";
            _taskService.Log(LogLevel.Success, "后端安装", "Python 后端依赖安装完成。");
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
            _taskService.Log(LogLevel.Error, "后端安装", ex.Message);
        }
        finally { IsBusy = false; }
    }

    [RelayCommand]
    private async Task SaveSettingsAsync()
    {
        try
        {
            _taskService.MaxConcurrency = Settings.EnableConcurrentDispatch ? Settings.MaxConcurrentTasks : 1;
        }
        catch (InvalidOperationException ex)
        {
            ErrorMessage = ex.Message;
            return;
        }
        await _settingsService.SaveSettingsAsync();
        _taskService.StaggerDelayMs = Settings.StaggerDelayMs;
        SuccessMessage = "全局系统偏好设置已成功保存！";
        _taskService.Log(LogLevel.Success, "系统设置", "应用全局偏好与代理通知配置已保存。");
    }

    [RelayCommand]
    private async Task ReloadSettingsAsync()
    {
        await _settingsService.LoadSettingsAsync();
        SuccessMessage = "设置已从本地重新加载";
        _taskService.Log(LogLevel.Info, "系统设置", "已重新载入本地配置文件。");
    }
}
