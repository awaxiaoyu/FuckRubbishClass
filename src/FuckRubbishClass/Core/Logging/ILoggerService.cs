using FuckRubbishClass.Models;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.Core.Logging;

public interface ILoggerService
{
    ObservableCollection<LogEntry> LiveLogs { get; }
    
    void Log(LogLevel level, string category, string message, Exception? exception = null, object? payload = null);
    void Debug(string category, string message);
    void Info(string category, string message);
    void Success(string category, string message);
    void Warn(string category, string message, Exception? exception = null);
    void Error(string category, string message, Exception? exception = null);
    
    void Clear();
    Task ExportLogsAsync(string destinationFilePath);
    string GetCurrentLogFilePath();
}
