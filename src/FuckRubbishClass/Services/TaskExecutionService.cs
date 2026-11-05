using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.Collections.ObjectModel;

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
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;
    private CancellationTokenSource? _activeCts;

    public ObservableCollection<TaskJobModel> Jobs { get; } = new();
    public ObservableCollection<LogEntry> Logs => _logger.LiveLogs;
    public TaskJobModel? CurrentRunningJob { get; private set; }

    public TaskExecutionService(ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;

        _logger.Info("任务引擎", "跨平台任务执行引擎已就绪。");
    }

    public void Log(LogLevel level, string source, string message)
    {
        _logger.Log(level, source, message);
    }

    public void ClearLogs()
    {
        _logger.Clear();
    }

    public async Task StartJobAsync(TaskJobModel job, Func<CancellationToken, IProgress<double>, IProgress<string>, Task> work)
    {
        if (CurrentRunningJob != null && CurrentRunningJob.Status == TaskExecutionStatus.Running)
        {
            _logger.Warn("任务调度", $"当前已有任务在执行：{CurrentRunningJob.Title}，请等待完成或手动终止。");
            return;
        }

        _activeCts = new CancellationTokenSource();
        CurrentRunningJob = job;
        job.Status = TaskExecutionStatus.Running;
        job.StartTime = DateTime.Now;
        job.ProgressPercent = 0;
        job.CurrentStep = "初始化执行上下文…";

        if (!Jobs.Contains(job)) Jobs.Insert(0, job);

        _logger.Info("任务调度", $"🚀 开始执行任务 [{job.TypeDisplayName}] - {job.Title}");
        _eventBus.Publish(new TaskJobStartedEvent(job));

        var progressGauge = new Progress<double>(p =>
        {
            job.ProgressPercent = p;
            _eventBus.Publish(new TaskJobProgressEvent(job.Id, p, job.CurrentStep));
        });

        var stepReporter = new Progress<string>(s =>
        {
            job.CurrentStep = s;
            _logger.Info(job.TypeDisplayName, s);
            _eventBus.Publish(new TaskJobProgressEvent(job.Id, job.ProgressPercent, s));
        });

        try
        {
            await Task.Run(() => work(_activeCts.Token, progressGauge, stepReporter), _activeCts.Token);
            job.Status = TaskExecutionStatus.Completed;
            job.ProgressPercent = 100;
            job.CurrentStep = "执行完成";
            _logger.Success("任务调度", $"✅ 任务完成：{job.Title}");
            _eventBus.Publish(new TaskJobCompletedEvent(job, true));
        }
        catch (OperationCanceledException)
        {
            job.Status = TaskExecutionStatus.Cancelled;
            job.CurrentStep = "用户已取消";
            _logger.Warn("任务调度", $"⏹ 任务已由用户手动停止：{job.Title}");
            _eventBus.Publish(new TaskJobCancelledEvent(job.Id));
            _eventBus.Publish(new TaskJobCompletedEvent(job, false, "用户取消"));
        }
        catch (Exception ex)
        {
            job.Status = TaskExecutionStatus.Failed;
            job.CurrentStep = $"执行异常: {ex.Message}";
            _logger.Error("任务调度", $"❌ 任务异常终止：{ex.Message}", ex);
            _eventBus.Publish(new TaskJobCompletedEvent(job, false, ex.Message));
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
            _logger.Warn("任务调度", "已发出任务终止指令…");
        }
        else
        {
            var job = Jobs.FirstOrDefault(x => x.Id == jobId);
            if (job != null)
            {
                job.Status = TaskExecutionStatus.Cancelled;
                _eventBus.Publish(new TaskJobCancelledEvent(jobId));
            }
        }
    }
}
