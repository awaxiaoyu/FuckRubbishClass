using FuckRubbishClass.Models;
using System.Collections.ObjectModel;
using System.Windows;

namespace FuckRubbishClass.Services;

public interface ITaskExecutionService
{
    ObservableCollection<TaskJobModel> Jobs { get; }
    ObservableCollection<LogEntry> Logs { get; }
    TaskJobModel? CurrentRunningJob { get; }
    void Log(LogLevel level, string source, string message);
    void ClearLogs();
    Task StartJobAsync(TaskJobModel job, Func<CancellationToken, IProgress<double>, IProgress<string>, Task> work);
    void CancelJob(string jobId);
}

public class TaskExecutionService : ITaskExecutionService
{
    private readonly SynchronizationContext? _syncContext = SynchronizationContext.Current;
    private CancellationTokenSource? _activeCts;

    public ObservableCollection<TaskJobModel> Jobs { get; } = new();
    public ObservableCollection<LogEntry> Logs { get; } = new();
    public TaskJobModel? CurrentRunningJob { get; private set; }

    public TaskExecutionService()
    {
        Log(LogLevel.Info, "系统", "FuckRubbishClass 现代化 Fluent 工作台已就绪。");
    }

    public void Log(LogLevel level, string source, string message)
    {
        void Add()
        {
            Logs.Add(new LogEntry
            {
                Level = level,
                Source = source,
                Message = message
            });
            // Keep recent 1000 logs
            if (Logs.Count > 1000) Logs.RemoveAt(0);
        }

        if (Application.Current?.Dispatcher != null && !Application.Current.Dispatcher.CheckAccess())
        {
            Application.Current.Dispatcher.InvokeAsync(Add);
        }
        else
        {
            Add();
        }
    }

    public void ClearLogs()
    {
        Logs.Clear();
        Log(LogLevel.Info, "系统", "日志控制台已清空。");
    }

    public async Task StartJobAsync(TaskJobModel job, Func<CancellationToken, IProgress<double>, IProgress<string>, Task> work)
    {
        if (CurrentRunningJob != null && CurrentRunningJob.Status == TaskExecutionStatus.Running)
        {
            Log(LogLevel.Warning, "调度", $"当前已有任务在执行：{CurrentRunningJob.Title}，请等待完成或手动终止。");
            return;
        }

        _activeCts = new CancellationTokenSource();
        CurrentRunningJob = job;
        job.Status = TaskExecutionStatus.Running;
        job.StartTime = DateTime.Now;
        job.ProgressPercent = 0;
        job.CurrentStep = "初始化执行上下文…";

        if (!Jobs.Contains(job)) Jobs.Insert(0, job);

        Log(LogLevel.Info, "任务调度", $"🚀 开始执行任务 [{job.TypeDisplayName}] - {job.Title}");

        var progressGauge = new Progress<double>(p =>
        {
            job.ProgressPercent = p;
        });

        var stepReporter = new Progress<string>(s =>
        {
            job.CurrentStep = s;
            Log(LogLevel.Info, job.TypeDisplayName, s);
        });

        try
        {
            await Task.Run(() => work(_activeCts.Token, progressGauge, stepReporter), _activeCts.Token);
            job.Status = TaskExecutionStatus.Completed;
            job.ProgressPercent = 100;
            job.CurrentStep = "执行完成";
            Log(LogLevel.Success, "任务调度", $"✅ 任务完成：{job.Title}");
        }
        catch (OperationCanceledException)
        {
            job.Status = TaskExecutionStatus.Cancelled;
            job.CurrentStep = "用户已取消";
            Log(LogLevel.Warning, "任务调度", $"⏹ 任务已由用户手动停止：{job.Title}");
        }
        catch (Exception ex)
        {
            job.Status = TaskExecutionStatus.Failed;
            job.CurrentStep = $"执行异常: {ex.Message}";
            Log(LogLevel.Error, "任务调度", $"❌ 任务异常终止：{ex.Message}");
        }
        finally
        {
            CurrentRunningJob = null;
            _activeCts?.Dispose();
            _activeCts = null;
        }
    }

    public void CancelJob(string jobId)
    {
        if (CurrentRunningJob?.Id == jobId)
        {
            _activeCts?.Cancel();
            Log(LogLevel.Warning, "调度", "已发出任务终止指令…");
        }
        else
        {
            var job = Jobs.FirstOrDefault(x => x.Id == jobId);
            if (job != null)
            {
                job.Status = TaskExecutionStatus.Cancelled;
            }
        }
    }
}
