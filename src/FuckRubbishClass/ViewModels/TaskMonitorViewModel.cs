using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.IO;
using System.Windows;

namespace FuckRubbishClass.ViewModels;

public partial class TaskMonitorViewModel : ViewModelBase
{
    private readonly ITaskExecutionService _taskService;
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;

    public ObservableCollection<TaskJobModel> Jobs => _taskService.Jobs;
    public ObservableCollection<LogEntry> AllLogs => _logger.LiveLogs;
    public ObservableCollection<LogEntry> FilteredLogs { get; } = new();

    public TaskJobModel? CurrentRunningJob => _taskService.CurrentRunningJob;

    [ObservableProperty]
    private string _selectedLogLevelFilter = "全部";

    [ObservableProperty]
    private TaskJobModel? _selectedJob;

    [ObservableProperty]
    private bool _autoScroll = true;

    [ObservableProperty]
    private string _currentLogFilePath = string.Empty;

    public TaskMonitorViewModel(
        ITaskExecutionService taskService,
        ILoggerService? logger = null,
        IEventBus? eventBus = null)
    {
        _taskService = taskService;
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;

        SelectedJob = _taskService.Jobs.FirstOrDefault();
        CurrentLogFilePath = _logger.GetCurrentLogFilePath();

        RefreshFilteredLogs();
        AllLogs.CollectionChanged += (_, _) => RefreshFilteredLogs();

        // 订阅全局任务事件
        _eventBus.Subscribe<TaskJobStartedEvent>(this, e =>
        {
            SelectedJob = e.Job;
            OnPropertyChanged(nameof(CurrentRunningJob));
        });

        _eventBus.Subscribe<TaskJobCompletedEvent>(this, e =>
        {
            OnPropertyChanged(nameof(CurrentRunningJob));
        });
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
        _logger.Clear();
        RefreshFilteredLogs();
        SuccessMessage = "实时日志控制台已清空";
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

    [RelayCommand]
    private async Task ExportLogsToFileAsync()
    {
        try
        {
            var exportPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, $"exported_logs_{DateTime.Now:yyyyMMdd_HHmmss}.txt");
            await _logger.ExportLogsAsync(exportPath);
            SuccessMessage = $"日志成功导出至: {Path.GetFileName(exportPath)}";
        }
        catch (Exception ex)
        {
            ErrorMessage = $"导出失败: {ex.Message}";
        }
    }
}
