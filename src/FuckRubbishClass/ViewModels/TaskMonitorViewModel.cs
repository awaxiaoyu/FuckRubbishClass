using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.Windows;

namespace FuckRubbishClass.ViewModels;

public partial class TaskMonitorViewModel : ViewModelBase
{
    private readonly ITaskExecutionService _taskService;

    public ObservableCollection<TaskJobModel> Jobs => _taskService.Jobs;
    public ObservableCollection<LogEntry> AllLogs => _taskService.Logs;
    public ObservableCollection<LogEntry> FilteredLogs { get; } = new();

    public TaskJobModel? CurrentRunningJob => _taskService.CurrentRunningJob;

    [ObservableProperty]
    private string _selectedLogLevelFilter = "全部";

    [ObservableProperty]
    private TaskJobModel? _selectedJob;

    [ObservableProperty]
    private bool _autoScroll = true;

    public TaskMonitorViewModel(ITaskExecutionService taskService)
    {
        _taskService = taskService;
        SelectedJob = _taskService.Jobs.FirstOrDefault();

        RefreshFilteredLogs();
        _taskService.Logs.CollectionChanged += (_, _) => RefreshFilteredLogs();
    }

    partial void OnSelectedLogLevelFilterChanged(string value) => RefreshFilteredLogs();

    private void RefreshFilteredLogs()
    {
        FilteredLogs.Clear();
        var query = AllLogs.AsEnumerable();

        if (SelectedLogLevelFilter == "信息") query = query.Where(x => x.Level == LogLevel.Info);
        else if (SelectedLogLevelFilter == "成功") query = query.Where(x => x.Level == LogLevel.Success);
        else if (SelectedLogLevelFilter == "警告") query = query.Where(x => x.Level == LogLevel.Warning);
        else if (SelectedLogLevelFilter == "错误") query = query.Where(x => x.Level == LogLevel.Error);

        foreach (var l in query) FilteredLogs.Add(l);
    }

    [RelayCommand]
    private void CancelJob(TaskJobModel? job)
    {
        var target = job ?? SelectedJob ?? CurrentRunningJob;
        if (target != null)
        {
            _taskService.CancelJob(target.Id);
            SuccessMessage = $"已停止任务：{target.Title}";
        }
    }

    [RelayCommand]
    private void ClearLogs()
    {
        _taskService.ClearLogs();
        RefreshFilteredLogs();
        SuccessMessage = "日志已清空";
    }

    [RelayCommand]
    private void CopyLogsToClipboard()
    {
        try
        {
            var text = string.Join(Environment.NewLine, FilteredLogs.Select(x => $"[{x.TimeFormatted}] [{x.Level}] [{x.Source}] {x.Message}"));
            Clipboard.SetText(text);
            SuccessMessage = "已复制日志至剪贴板";
        }
        catch (Exception ex)
        {
            ErrorMessage = $"复制失败: {ex.Message}";
        }
    }
}
