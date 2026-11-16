using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Windows;

namespace FuckRubbishClass.Services;

public interface ITaskExecutionService
{
    ObservableCollection<TaskJobModel> Jobs { get; }
    ObservableCollection<TaskJobModel> RunningJobs { get; }
    ObservableCollection<TaskJobModel> PendingJobs { get; }
    ObservableCollection<TaskJobModel> FinishedJobs { get; }
    ObservableCollection<LogEntry> Logs { get; }
    
    TaskJobModel? CurrentRunningJob { get; }
    int MaxConcurrency { get; set; }
    int StaggerDelayMs { get; set; }
    int ActiveWorkerCount { get; }
    
    void Log(LogLevel level, string source, string message);
    void ClearLogs();
    
    Task StartJobAsync(TaskJobModel job, Func<CancellationToken, IProgress<double>, IProgress<string>, Task> work);
    Task EnqueueBatchJobsAsync(IEnumerable<TaskJobModel> jobs, Func<TaskJobModel, CancellationToken, IProgress<double>, IProgress<string>, Task> workerFactory);
    void CancelJob(string jobId);
    void CancelAllJobs();
}

public class TaskExecutionService : ITaskExecutionService
{
    private readonly ILoggerService _logger;
    private readonly IEventBus _eventBus;
    private SemaphoreSlim _concurrencySemaphore = new(3, 8);
    private readonly ConcurrentDictionary<string, SemaphoreSlim> _accountLocks = new();
    private readonly ConcurrentDictionary<string, CancellationTokenSource> _jobCancellations = new();
    private int _workerIdCounter;

    public ObservableCollection<TaskJobModel> Jobs { get; } = new();
    public ObservableCollection<TaskJobModel> RunningJobs { get; } = new();
    public ObservableCollection<TaskJobModel> PendingJobs { get; } = new();
    public ObservableCollection<TaskJobModel> FinishedJobs { get; } = new();
    public ObservableCollection<LogEntry> Logs => _logger.LiveLogs;
    public TaskJobModel? CurrentRunningJob => RunningJobs.FirstOrDefault();

    private int _maxConcurrency = 3;
    public int MaxConcurrency
    {
        get => _maxConcurrency;
        set
        {
            var capacity = Math.Clamp(value, 1, 8);
            if (capacity == _maxConcurrency) return;
            if (!_jobCancellations.IsEmpty) throw new InvalidOperationException("请等待当前任务结束后修改并发数。");
            _concurrencySemaphore.Dispose();
            _concurrencySemaphore = new SemaphoreSlim(capacity, 8);
            _maxConcurrency = capacity;
        }
    }
    public int ActiveWorkerCount => RunningJobs.Count;
    public int StaggerDelayMs { get; set; }

    public TaskExecutionService(ILoggerService? logger = null, IEventBus? eventBus = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _eventBus = eventBus ?? AppEventBus.Instance;

        _logger.Info("任务引擎", $"跨平台多账号多线程并发引擎已初始化 (最大全局并发度: {MaxConcurrency})。");
    }

    public void Log(LogLevel level, string source, string message) => _logger.Log(level, source, message);
    public void ClearLogs() => _logger.Clear();

    public async Task StartJobAsync(TaskJobModel job, Func<CancellationToken, IProgress<double>, IProgress<string>, Task> work)
    {
        await EnqueueBatchJobsAsync([job], (j, token, p, s) => work(token, p, s));
    }

    public async Task EnqueueBatchJobsAsync(
        IEnumerable<TaskJobModel> jobs,
        Func<TaskJobModel, CancellationToken, IProgress<double>, IProgress<string>, Task> workerFactory)
    {
        var jobList = jobs.ToList();
        if (jobList.Count == 0) return;

        var position = 0;
        foreach (var job in jobList)
        {
            if (!_jobCancellations.TryAdd(job.Id, new CancellationTokenSource()))
                throw new InvalidOperationException("同一个任务不能重复入队。");
            job.Status = TaskExecutionStatus.Queued;
            job.CurrentStep = "在并发调度队列中等待…";

            DispatchToUi(() =>
            {
                if (!Jobs.Contains(job)) Jobs.Insert(0, job);
                if (!PendingJobs.Contains(job)) PendingJobs.Add(job);
            });

            _logger.Info("并发调度", $"[排队] 任务已加入待执行队列: [{job.PlatformDisplayName}] [{job.AccountName}] {job.Title}");
            
            // 异步后台启动工作单元，由 Semaphore 控制真正进入并发执行的时间点
            var startDelay = position++ * Math.Max(0, StaggerDelayMs);
            _ = Task.Run(() => ExecuteWorkerPipelineAsync(job, workerFactory, startDelay));
        }

        await Task.CompletedTask;
    }

    private async Task ExecuteWorkerPipelineAsync(
        TaskJobModel job,
        Func<TaskJobModel, CancellationToken, IProgress<double>, IProgress<string>, Task> workerFactory,
        int startDelay)
    {
        var cts = _jobCancellations[job.Id];
        var accountAcquired = false;
        var slotAcquired = false;

        // 单账号并发锁 (避免同一账号同时打开多路视频流被网课平台风控判定多端异常学习)
        var accountKey = $"{job.Platform}:{(string.IsNullOrWhiteSpace(job.AccountId) ? job.AccountName : job.AccountId)}";
        var accountLock = _accountLocks.GetOrAdd(accountKey, _ => new SemaphoreSlim(1, 1));

        try
        {
            if (startDelay > 0) await Task.Delay(startDelay, cts.Token);
            // 1. 等待单账号排队锁
            await accountLock.WaitAsync(cts.Token);
            accountAcquired = true;

            // 2. 等待全局并发槽位
            await _concurrencySemaphore.WaitAsync(cts.Token);
            slotAcquired = true;

            var workerTag = $"Worker-{Interlocked.Increment(ref _workerIdCounter)}";
            job.WorkerThreadTag = workerTag;
            job.Status = TaskExecutionStatus.Running;
            job.StartTime = DateTime.Now;
            job.CurrentStep = "已分配并发槽位，正在建立平台独立通信会话…";

            DispatchToUi(() =>
            {
                PendingJobs.Remove(job);
                if (!RunningJobs.Contains(job)) RunningJobs.Add(job);
            });

            _logger.Info(workerTag, $"🚀 启动并发线程 [{workerTag}] -> 账号 [{job.AccountName}] 课程《{job.CourseName}》");
            _eventBus.Publish(new TaskJobStartedEvent(job));

            var progressGauge = new Progress<double>(p =>
            {
                DispatchToUi(() =>
                {
                    if (job.Status != TaskExecutionStatus.Running) return;
                    job.ProgressPercent = p;
                    _eventBus.Publish(new TaskJobProgressEvent(job.Id, p, job.CurrentStep));
                });
            });

            var stepReporter = new Progress<string>(s =>
            {
                DispatchToUi(() =>
                {
                    if (job.Status == TaskExecutionStatus.Running) job.CurrentStep = s;
                });
                _logger.Info($"[{job.AccountName}]", s);
                _eventBus.Publish(new TaskJobProgressEvent(job.Id, job.ProgressPercent, s));
            });

            // 执行真正的刷课/作答作业
            await workerFactory(job, cts.Token, progressGauge, stepReporter);
            cts.Token.ThrowIfCancellationRequested();

            job.Status = TaskExecutionStatus.Completed;
            job.ProgressPercent = 100;
            job.CurrentStep = "后端任务处理结束";
            _logger.Success(workerTag, $"✅ [{job.AccountName}] 《{job.CourseName}》并发任务执行完毕！");
        }
        catch (OperationCanceledException)
        {
            job.Status = TaskExecutionStatus.Cancelled;
            job.CurrentStep = "用户已手动停止";
            _logger.Warn(job.WorkerThreadTag, $"⏹ [{job.AccountName}] 《{job.CourseName}》任务已终止。");
            _eventBus.Publish(new TaskJobCancelledEvent(job.Id));
        }
        catch (Exception ex)
        {
            job.Status = TaskExecutionStatus.Failed;
            job.ErrorMessage = ex.Message;
            job.CurrentStep = $"异常: {ex.Message}";
            _logger.Error(job.WorkerThreadTag, $"❌ [{job.AccountName}] 《{job.CourseName}》任务异常终止: {ex.Message}", ex);
        }
        finally
        {
            // 归还槽位与清理资源

            DispatchToUi(() =>
            {
                PendingJobs.Remove(job);
                RunningJobs.Remove(job);
                if (!FinishedJobs.Contains(job)) FinishedJobs.Insert(0, job);
            });

            if (slotAcquired) _concurrencySemaphore.Release();
            if (accountAcquired) accountLock.Release();
            _jobCancellations.TryRemove(job.Id, out _);
            cts.Dispose();
            _eventBus.Publish(new TaskJobCompletedEvent(job, job.Status == TaskExecutionStatus.Completed, job.ErrorMessage));
        }
    }

    public void CancelJob(string jobId)
    {
        if (_jobCancellations.TryGetValue(jobId, out var cts))
        {
            try { cts.Cancel(); }
            catch (ObjectDisposedException) { return; }
            _logger.Warn("任务调度", $"已发出终止指令给任务 ID: {jobId}");
        }
        else
        {
            var job = PendingJobs.FirstOrDefault(x => x.Id == jobId);
            if (job != null)
            {
                job.Status = TaskExecutionStatus.Cancelled;
                job.CurrentStep = "在排队队列中被取消";
                DispatchToUi(() =>
                {
                    PendingJobs.Remove(job);
                    FinishedJobs.Insert(0, job);
                });
                _eventBus.Publish(new TaskJobCancelledEvent(jobId));
            }
        }
    }

    public void CancelAllJobs()
    {
        _logger.Warn("任务调度", "正在一键终止所有运行中与排队中的并发任务…");
        
        foreach (var pair in _jobCancellations)
        {
            try
            {
                pair.Value.Cancel();
            }
            catch { }
        }

    }

    private static void DispatchToUi(Action action)
    {
        if (Application.Current?.Dispatcher != null && !Application.Current.Dispatcher.CheckAccess())
        {
            if (!Application.Current.Dispatcher.HasShutdownStarted)
                Application.Current.Dispatcher.Invoke(action);
        }
        else
        {
            action();
        }
    }
}
