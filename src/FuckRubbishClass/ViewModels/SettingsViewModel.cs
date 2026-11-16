using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;

namespace FuckRubbishClass.ViewModels;

public partial class SettingsViewModel : ViewModelBase
{
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _taskService;

    public AppSettings Settings => _settingsService.AppSettings;

    public SettingsViewModel(ISettingsService settingsService, ITaskExecutionService taskService)
    {
        _settingsService = settingsService;
        _taskService = taskService;
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
