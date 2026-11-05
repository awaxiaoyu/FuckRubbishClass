using FuckRubbishClass.Models;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;

namespace FuckRubbishClass.Core.Logging;

public class LoggerService : ILoggerService, IDisposable
{
    private readonly string _logDirectory;
    private readonly BlockingCollection<string> _logQueue = new(new ConcurrentQueue<string>());
    private readonly Thread _writeThread;
    private readonly CancellationTokenSource _cts = new();
    private const int MaxInMemoryLogs = 1500;

    public ObservableCollection<LogEntry> LiveLogs { get; } = new();

    public LoggerService(string? customLogDir = null)
    {
        _logDirectory = customLogDir ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "logs");
        Directory.CreateDirectory(_logDirectory);

        _writeThread = new Thread(ProcessLogQueue)
        {
            IsBackground = true,
            Name = "FuckRubbishClass_LogWriterThread"
        };
        _writeThread.Start();

        Info("系统", "全局日志系统已启动，日志将持久化落盘至: " + GetCurrentLogFilePath());
    }

    public string GetCurrentLogFilePath()
    {
        var fileName = $"app-{DateTime.Now:yyyy-MM-dd}.log";
        return Path.Combine(_logDirectory, fileName);
    }

    public void Log(LogLevel level, string category, string message, Exception? exception = null, object? payload = null)
    {
        var now = DateTime.Now;
        var entry = new LogEntry
        {
            Timestamp = now,
            Level = level,
            Source = string.IsNullOrWhiteSpace(category) ? "全局" : category,
            Message = message
        };

        if (exception != null)
        {
            entry.Message += $"{Environment.NewLine}  [异常堆栈] {exception.GetType().Name}: {exception.Message}{Environment.NewLine}{exception.StackTrace}";
        }

        // 1. Dispatch to UI ObservableCollection safely
        PushToUi(entry);

        // 2. Format line for file & trace
        var levelTag = level switch
        {
            LogLevel.Info => "INFO ",
            LogLevel.Success => "SUCC ",
            LogLevel.Warning => "WARN ",
            LogLevel.Error => "ERROR",
            LogLevel.Debug => "DEBUG",
            _ => "INFO "
        };

        var logLine = $"[{now:yyyy-MM-dd HH:mm:ss.fff}] [{levelTag}] [{entry.Source}] {entry.Message}";

        // 3. Debug Output
        Trace.WriteLine(logLine);

        // 4. Enqueue for asynchronous disk persistence
        if (!_logQueue.IsAddingCompleted)
        {
            _logQueue.TryAdd(logLine);
        }
    }

    private void PushToUi(LogEntry entry)
    {
        void Add()
        {
            LiveLogs.Add(entry);
            if (LiveLogs.Count > MaxInMemoryLogs)
            {
                LiveLogs.RemoveAt(0);
            }
        }

        if (Application.Current?.Dispatcher != null && !Application.Current.Dispatcher.CheckAccess())
        {
            Application.Current.Dispatcher.InvokeAsync(Add, System.Windows.Threading.DispatcherPriority.Background);
        }
        else
        {
            Add();
        }
    }

    private void ProcessLogQueue()
    {
        while (!_cts.Token.IsCancellationRequested)
        {
            try
            {
                if (_logQueue.TryTake(out var line, 1000, _cts.Token))
                {
                    var filePath = GetCurrentLogFilePath();
                    File.AppendAllText(filePath, line + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch
            {
                // Prevent crash in background logger
            }
        }

        // Drain remaining
        try
        {
            var filePath = GetCurrentLogFilePath();
            while (_logQueue.TryTake(out var line))
            {
                File.AppendAllText(filePath, line + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch
        {
            // Ignore drain failure on exit
        }
    }

    public void Debug(string category, string message) => Log(LogLevel.Debug, category, message);
    public void Info(string category, string message) => Log(LogLevel.Info, category, message);
    public void Success(string category, string message) => Log(LogLevel.Success, category, message);
    public void Warn(string category, string message, Exception? exception = null) => Log(LogLevel.Warning, category, message, exception);
    public void Error(string category, string message, Exception? exception = null) => Log(LogLevel.Error, category, message, exception);

    public void Clear()
    {
        void DoClear()
        {
            LiveLogs.Clear();
        }

        if (Application.Current?.Dispatcher != null && !Application.Current.Dispatcher.CheckAccess())
        {
            Application.Current.Dispatcher.Invoke(DoClear);
        }
        else
        {
            DoClear();
        }

        Info("系统", "控制台实时日志已清空。");
    }

    public async Task ExportLogsAsync(string destinationFilePath)
    {
        var sb = new StringBuilder();
        foreach (var log in LiveLogs.ToList())
        {
            sb.AppendLine($"[{log.TimeFormatted}] [{log.Level}] [{log.Source}] {log.Message}");
        }
        await File.WriteAllTextAsync(destinationFilePath, sb.ToString(), Encoding.UTF8);
        Success("系统", $"日志已成功导出至: {destinationFilePath}");
    }

    public void Dispose()
    {
        _logQueue.CompleteAdding();
        _cts.Cancel();
        _writeThread.Join(1500);
        _cts.Dispose();
        _logQueue.Dispose();
    }
}
